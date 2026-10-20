"""Telemetry ingestion, persistence and retrieval."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.core.metrics import TELEMETRY_TOTAL, TELEMETRY_PROCESSING_SECONDS
from app.core.state import app_state
from app.db.models import Device, Telemetry
from app.schemas.common import TelemetryIn, TelemetryList, TelemetryOut
from app.services import device_service

log = get_logger("telemetry_service")

# Numeric readings we promote to indexed columns when present.
NUMERIC_FIELDS = {
    "temperature": "temperature",
    "temp": "temperature",
    "humidity": "humidity",
    "energy_w": "energy_w",
    "power_w": "energy_w",
    "power": "energy_w",
    "voltage": "voltage",
    "current_a": "current_a",
    "current": "current_a",
    "rssi": "rssi",
}

MAX_READING_STRING_LEN = 512


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _coerce_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (ValueError, AttributeError):
            return None
    return None


def sanitize_readings(readings: Dict[str, Any]) -> Dict[str, Any]:
    """Keep JSON-serialisable scalars; drop oversized / unsafe values."""
    clean: Dict[str, Any] = {}
    for key, value in list(readings.items())[:64]:
        if not isinstance(key, str) or len(key) > 64:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            clean[key] = float(value)
        elif isinstance(value, bool):
            clean[key] = value
        elif isinstance(value, str):
            if len(value) <= MAX_READING_STRING_LEN:
                clean[key] = value
        elif value is None:
            clean[key] = None
        # nested dicts/lists are intentionally not stored
    return clean


def ingest_telemetry(
    session: Session,
    payload: TelemetryIn,
    *,
    topic: str = "",
    auto_register: bool = True,
    run_detection: bool = True,
) -> Telemetry:
    """Validate, persist and (optionally) analyse a single telemetry record."""
    start = __import__("time").perf_counter()
    readings = sanitize_readings(payload.readings)

    device = session.get(Device, payload.device_id)
    if device is None:
        if not auto_register:
            raise KeyError(f"Unknown device '{payload.device_id}'")
        device = device_service.upsert_device(
            session, payload.device_id, status=payload.status
        )

    ts = payload.timestamp or _utcnow()
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)

    columns: Dict[str, Optional[float]] = {v: None for v in
                                           set(NUMERIC_FIELDS.values())}
    for raw_key, value in readings.items():
        col = NUMERIC_FIELDS.get(str(raw_key).lower())
        if col:
            num = _coerce_float(value)
            if num is not None:
                columns[col] = num

    row = Telemetry(
        device_id=payload.device_id,
        timestamp=ts,
        topic=topic or f"iot/devices/{payload.device_id}/telemetry",
        device_status=payload.status,
        readings_json=json.dumps(readings, default=str, separators=(",", ":")),
        is_synthetic=payload.is_synthetic,
        ingest_source=payload.ingest_source,
        **columns,
    )
    session.add(row)
    session.flush()

    device_service.touch_message(session, device, ts)
    app_state.note_latest_telemetry(device.device_id, row.id)

    TELEMETRY_TOTAL.labels(source=payload.ingest_source).inc()

    if run_detection:
        # Lazy import: detection_service consumes telemetry, telemetry_service
        # triggers detection - a module-level import would be circular.
        from app.services import detection_service
        try:
            detection_service.analyse_telemetry(session, row, device)
        except Exception as exc:  # pragma: no cover - defensive
            log.error("Detection failed for telemetry %s: %s", row.id, exc)
            from app.core.metrics import ERRORS_TOTAL
            ERRORS_TOTAL.labels(kind="detection").inc()

    elapsed = __import__("time").perf_counter() - start
    TELEMETRY_PROCESSING_SECONDS.observe(elapsed)
    return row


def list_telemetry(
    session: Session,
    *,
    device_id: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0,
) -> TelemetryList:
    stmt = select(Telemetry)
    if device_id:
        stmt = stmt.where(Telemetry.device_id == device_id)
    if since:
        stmt = stmt.where(Telemetry.timestamp >= since)
    if until:
        stmt = stmt.where(Telemetry.timestamp <= until)

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows: Sequence[Telemetry] = session.scalars(
        stmt.order_by(Telemetry.timestamp.desc()).limit(limit).offset(offset)
    ).all()

    items = []
    for row in rows:
        out = TelemetryOut.model_validate(row)
        out.readings = row.readings
        items.append(out)
    return TelemetryList(
        items=items,
        meta={"total": total, "limit": limit, "offset": offset},
    )


def latest_for_device(session: Session, device_id: str) -> Optional[Telemetry]:
    return session.scalars(
        select(Telemetry)
        .where(Telemetry.device_id == device_id)
        .order_by(Telemetry.timestamp.desc())
        .limit(1)
    ).first()


def recent_sequence(session: Session, device_id: str,
                    limit: int = 50) -> List[Telemetry]:
    """Most recent telemetry for one device, oldest first."""
    rows = session.scalars(
        select(Telemetry)
        .where(Telemetry.device_id == device_id)
        .order_by(Telemetry.timestamp.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))


def count_telemetry(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Telemetry)) or 0


def count_since(session: Session, since: datetime) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(Telemetry)
            .where(Telemetry.timestamp >= since)
        )
        or 0
    )
