"""Telemetry ingestion and retrieval."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.db.database import get_db
from app.schemas.common import TelemetryIn, TelemetryList, TelemetryOut
from app.services import telemetry_service

router = APIRouter(prefix="/telemetry", tags=["telemetry"])
log = get_logger("api.telemetry")


@router.get("", response_model=TelemetryList, summary="List telemetry records")
def list_telemetry(
    device_id: Optional[str] = Query(None),
    minutes: Optional[int] = Query(None, ge=1, le=60 * 24 * 30,
                                    description="Only records from the last N minutes"),
    since: Optional[datetime] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> TelemetryList:
    if minutes is not None:
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            minutes=minutes
        )
    return telemetry_service.list_telemetry(
        session, device_id=device_id, since=since,
        limit=limit, offset=offset,
    )


@router.get("/latest/{device_id}", response_model=TelemetryOut,
            summary="Most recent record for a device")
def latest(device_id: str, session: Session = Depends(get_db)) -> TelemetryOut:
    row = telemetry_service.latest_for_device(session, device_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No telemetry for this device")
    out = TelemetryOut.model_validate(row)
    out.readings = row.readings
    return out


@router.post("", response_model=TelemetryOut, status_code=201,
             summary="Ingest a telemetry record")
def ingest(payload: TelemetryIn, session: Session = Depends(get_db)) -> TelemetryOut:
    """Manual ingestion endpoint (also used by tests and external tooling).

    Records posted here are validated, stored and run through the detector
    exactly like MQTT messages.
    """
    try:
        if not payload.ingest_source:
            payload.ingest_source = "api"
        row = telemetry_service.ingest_telemetry(session, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    session.commit()
    out = TelemetryOut.model_validate(row)
    out.readings = row.readings
    return out


@router.get("/stats/{device_id}", summary="Per-device telemetry counters")
def device_stats(device_id: str,
                 session: Session = Depends(get_db)) -> dict:
    from app.db.models import Telemetry
    from sqlalchemy import func, select

    total = session.scalar(
        select(func.count()).select_from(Telemetry)
        .where(Telemetry.device_id == device_id)
    ) or 0
    last_hour = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    recent = session.scalar(
        select(func.count()).select_from(Telemetry)
        .where(Telemetry.device_id == device_id, Telemetry.timestamp >= last_hour)
    ) or 0
    latest = telemetry_service.latest_for_device(session, device_id)
    return {
        "device_id": device_id,
        "total_messages": total,
        "messages_last_hour": recent,
        "latest_timestamp": (latest.timestamp.isoformat() + "Z") if latest else None,
        "source": latest.ingest_source if latest else None,
        "is_synthetic": latest.is_synthetic if latest else None,
    }
