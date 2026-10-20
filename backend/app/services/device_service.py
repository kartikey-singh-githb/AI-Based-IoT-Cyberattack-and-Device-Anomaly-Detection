"""Device registration / query service."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.db.models import Device, Telemetry
from app.schemas.common import DeviceCreate, DeviceOut, DeviceUpdate

log = get_logger("device_service")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_device_out(device: Device, latest: Optional[Telemetry]) -> DeviceOut:
    out = DeviceOut.model_validate(device)
    if latest is not None:
        out.latest_reading = {
            "timestamp": latest.timestamp.isoformat() + "Z",
            "temperature": latest.temperature,
            "humidity": latest.humidity,
            "energy_w": latest.energy_w,
            "voltage": latest.voltage,
            "current_a": latest.current_a,
            "rssi": latest.rssi,
            "readings": latest.readings,
            "status": latest.device_status,
        }
    else:
        out.latest_reading = None
    return out


def list_devices(
    session: Session,
    *,
    status: Optional[str] = None,
    device_type: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[List[DeviceOut], int]:
    stmt = select(Device)
    if status:
        stmt = stmt.where(Device.status == status)
    if device_type:
        stmt = stmt.where(Device.device_type == device_type)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(Device.name.ilike(like) | Device.device_id.ilike(like))

    total = session.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0

    stmt = stmt.order_by(Device.last_seen.desc()).limit(limit).offset(offset)
    devices: Sequence[Device] = session.scalars(stmt).all()

    # Batch-fetch latest telemetry to avoid N+1 queries
    latest_map = _latest_telemetry_map(session, [d.device_id for d in devices])

    return [
        to_device_out(d, latest_map.get(d.device_id)) for d in devices
    ], total


def _latest_telemetry_map(session: Session, device_ids: Sequence[str]) -> dict:
    if not device_ids:
        return {}
    sub = (
        select(
            Telemetry.device_id,
            func.max(Telemetry.timestamp).label("max_ts"),
        )
        .where(Telemetry.device_id.in_(list(device_ids)))
        .group_by(Telemetry.device_id)
        .subquery()
    )
    rows = session.execute(
        select(Telemetry).join(
            sub,
            (Telemetry.device_id == sub.c.device_id)
            & (Telemetry.timestamp == sub.c.max_ts),
        )
    ).scalars().all()
    return {r.device_id: r for r in rows}


def get_device(session: Session, device_id: str) -> Optional[Device]:
    return session.get(Device, device_id)


def get_device_out(session: Session, device_id: str) -> Optional[DeviceOut]:
    device = get_device(session, device_id)
    if device is None:
        return None
    latest = session.scalars(
        select(Telemetry)
        .where(Telemetry.device_id == device_id)
        .order_by(Telemetry.timestamp.desc())
        .limit(1)
    ).first()
    return to_device_out(device, latest)


def create_device(session: Session, payload: DeviceCreate) -> Device:
    if get_device(session, payload.device_id) is not None:
        raise ValueError(f"Device '{payload.device_id}' already exists")

    device = Device(
        device_id=payload.device_id,
        name=payload.name,
        device_type=payload.device_type,
        status=payload.status,
        location=payload.location,
        firmware=payload.firmware,
        is_simulated=payload.is_simulated,
        behaviour_label=payload.behaviour_label,
        expected_interval_s=payload.expected_interval_s,
        meta_json=json.dumps(payload.meta, default=str),
        first_seen=_utcnow(),
        last_seen=_utcnow(),
    )
    session.add(device)
    session.flush()
    log.info("Device registered: %s (%s)", device.device_id, device.device_type)
    return device


def update_device(session: Session, device: Device,
                  payload: DeviceUpdate) -> Device:
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(device, key, value)
    device.last_seen = _utcnow()
    session.flush()
    return device


def delete_device(session: Session, device: Device) -> None:
    session.delete(device)
    session.flush()
    log.info("Device deleted: %s", device.device_id)


def upsert_device(
    session: Session,
    device_id: str,
    *,
    name: Optional[str] = None,
    device_type: str = "unknown",
    status: str = "online",
    location: Optional[str] = None,
    firmware: Optional[str] = None,
    is_simulated: Optional[bool] = None,
    behaviour_label: Optional[str] = None,
    expected_interval_s: Optional[float] = None,
) -> Device:
    """Get-or-create used by the MQTT subscriber and simulator.

    Only the fields a caller actually passes are written, so an ordinary
    telemetry ingest (which knows nothing about firmware or intervals) can
    never overwrite settings established at registration time.
    """
    device = session.get(Device, device_id)
    created = False
    if device is None:
        device = Device(
            device_id=device_id,
            name=name or device_id,
            device_type=device_type,
            location=location or "",
            firmware=firmware or "1.0.0",
            is_simulated=True if is_simulated is None else is_simulated,
            status=status,
            behaviour_label=behaviour_label or "normal",
            expected_interval_s=expected_interval_s or 2.0,
            first_seen=_utcnow(),
        )
        session.add(device)
        created = True
    device.status = status
    device.last_seen = _utcnow()
    if name:
        device.name = name
    if device_type and device_type != "unknown":
        device.device_type = device_type
    if location is not None:
        device.location = location
    if firmware is not None:
        device.firmware = firmware
    if is_simulated is not None:
        device.is_simulated = is_simulated
    if behaviour_label is not None:
        device.behaviour_label = behaviour_label
    if expected_interval_s is not None:
        device.expected_interval_s = expected_interval_s
    session.flush()
    if created:
        log.info("Auto-registered device %s from stream", device_id)
    return device


def touch_message(session: Session, device: Device, when: Optional[datetime] = None) -> None:
    device.message_count += 1
    device.last_seen = when or _utcnow()
    if device.status != "online":
        device.status = "online"


def recompute_status(session: Session, device_id: str,
                     stale_after_s: float = 30.0) -> None:
    """Mark a device offline when it stops sending telemetry."""
    device = session.get(Device, device_id)
    if device is None or device.last_seen is None:
        return
    age = (_utcnow() - device.last_seen).total_seconds()
    if age > stale_after_s and device.status == "online":
        device.status = "offline"


def refresh_device_gauge(session: Session) -> None:
    """Update the Prometheus device gauge from real DB counts."""
    from app.core.metrics import DEVICES_GAUGE

    rows = session.execute(
        select(Device.status, func.count()).group_by(Device.status)
    ).all()
    counts = {status: count for status, count in rows}
    for status in ("online", "offline", "degraded", "maintenance"):
        DEVICES_GAUGE.labels(status=status).set(counts.get(status, 0))
