"""Anomaly detection results."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.common import AnomalyList, AnomalyOut
from app.services import detection_service

router = APIRouter(prefix="/anomalies", tags=["anomalies"])


@router.get("", response_model=AnomalyList, summary="List anomaly detections")
def list_anomalies(
    device_id: Optional[str] = Query(None),
    severity: Optional[str] = Query(None, pattern="^(normal|suspicious|critical)$"),
    detection_status: Optional[str] = Query(
        None, alias="status",
        pattern="^(new|investigating|resolved|false_positive)$",
    ),
    include_normal: bool = Query(
        False, description="Include scored-but-not-flagged records"
    ),
    minutes: Optional[int] = Query(None, ge=1, le=60 * 24 * 30),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> AnomalyList:
    since = None
    if minutes is not None:
        from datetime import timedelta, timezone

        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            minutes=minutes
        )
    return detection_service.list_anomalies(
        session, device_id=device_id, severity=severity,
        status=detection_status, only_anomalies=not include_normal,
        since=since, limit=limit, offset=offset,
    )


@router.get("/counts", summary="Detection counts by severity and status")
def counts(session: Session = Depends(get_db)) -> dict:
    return detection_service.counts(session)


@router.get("/{anomaly_id}", response_model=AnomalyOut, summary="Detection detail")
def get_anomaly(anomaly_id: int, session: Session = Depends(get_db)) -> AnomalyOut:
    event = detection_service.get_anomaly(session, anomaly_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    alert_id = None
    if event.alert is not None:
        alert_id = event.alert.id
    return AnomalyOut(
        id=event.id,
        device_id=event.device_id,
        timestamp=event.timestamp,
        score=event.score,
        is_anomaly=event.is_anomaly,
        severity=event.severity,
        threshold=event.threshold,
        model_version=event.model_version,
        description=event.description,
        status=event.status,
        origin=event.origin or "detector",
        is_synthetic=event.is_synthetic,
        features=event.features,
        deviations=event.deviations,
        alert_id=alert_id,
    )


@router.patch("/{anomaly_id}", response_model=AnomalyOut,
              summary="Update detection status")
def update_anomaly(
    anomaly_id: int,
    status: str = Query(..., pattern="^(new|investigating|resolved|false_positive)$"),
    session: Session = Depends(get_db),
) -> AnomalyOut:
    event = detection_service.get_anomaly(session, anomaly_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    try:
        detection_service.update_status(session, event, status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    session.commit()
    return get_anomaly(anomaly_id, session)
