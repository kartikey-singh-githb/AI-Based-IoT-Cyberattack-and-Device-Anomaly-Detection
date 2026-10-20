"""Security alerts: listing, inspection, acknowledgement, resolution."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.common import AlertCreate, AlertList, AlertOut, AlertUpdate
from app.services import alert_service

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=AlertList, summary="List security alerts")
def list_alerts(
    status_filter: Optional[str] = Query(None, alias="status",
                                         pattern="^(open|acknowledged|resolved)$"),
    severity: Optional[str] = Query(None,
                                    pattern="^(low|medium|high|critical)$"),
    device_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None, max_length=64),
    minutes: Optional[int] = Query(None, ge=1, le=60 * 24 * 90),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> AlertList:
    since = None
    if minutes is not None:
        from datetime import timedelta, timezone

        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            minutes=minutes
        )
    return alert_service.list_alerts(
        session, status=status_filter, severity=severity,
        device_id=device_id, category=category, since=since,
        limit=limit, offset=offset,
    )


@router.get("/counts", summary="Alert counts by status and severity")
def counts(session: Session = Depends(get_db)) -> dict:
    return alert_service.counts(session)


@router.get("/{alert_id}", response_model=AlertOut, summary="Alert detail")
def get_alert(alert_id: int, session: Session = Depends(get_db)) -> AlertOut:
    alert = alert_service.get_alert(session, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return AlertOut.model_validate(alert)


@router.post("", response_model=AlertOut, status_code=201,
             summary="Create a manual alert")
def create_alert(payload: AlertCreate,
                 session: Session = Depends(get_db)) -> AlertOut:
    alert = alert_service.create_manual(session, payload)
    session.commit()
    return AlertOut.model_validate(alert)


@router.patch("/{alert_id}", response_model=AlertOut,
              summary="Acknowledge / resolve / annotate an alert")
def update_alert(alert_id: int, payload: AlertUpdate,
                 session: Session = Depends(get_db)) -> AlertOut:
    alert = alert_service.get_alert(session, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    updated = alert_service.update_alert(session, alert, payload)
    session.commit()
    return AlertOut.model_validate(updated)


@router.post("/{alert_id}/acknowledge", response_model=AlertOut,
             summary="Acknowledge an alert")
def acknowledge(alert_id: int,
                assignee: str = Query("", max_length=128),
                session: Session = Depends(get_db)) -> AlertOut:
    alert = alert_service.acknowledge(session, alert_id, assignee)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    session.commit()
    return AlertOut.model_validate(alert)


@router.post("/{alert_id}/resolve", response_model= AlertOut,
             summary="Resolve an alert")
def resolve(alert_id: int,
            note: str = Query("", max_length=4000),
            session: Session = Depends(get_db)) -> AlertOut:
    alert = alert_service.resolve(session, alert_id, note)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    session.commit()
    return AlertOut.model_validate(alert)
