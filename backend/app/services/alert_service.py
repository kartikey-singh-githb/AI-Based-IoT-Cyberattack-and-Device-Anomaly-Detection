"""Security alert lifecycle: creation, filtering, acknowledgement, resolution."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger
from app.core.metrics import ALERTS_TOTAL, ALERTS_OPEN, ALERTS_UNRESOLVED
from app.db.models import AnomalyEvent, SecurityAlert
from app.schemas.common import AlertCreate, AlertList, AlertOut, AlertUpdate

log = get_logger("alert_service")

VALID_SEVERITIES = ("low", "medium", "high", "critical")
VALID_STATUSES = ("open", "acknowledged", "resolved")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_alert(
    session: Session,
    *,
    device_id: str,
    title: str,
    description: str = "",
    severity: str = "medium",
    category: str = "anomaly",
    anomaly_id: Optional[int] = None,
    origin: str = "detection",
    is_synthetic: bool = True,
    timestamp: Optional[datetime] = None,
) -> SecurityAlert:
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"Invalid severity '{severity}'")

    alert = SecurityAlert(
        anomaly_id=anomaly_id,
        device_id=device_id,
        timestamp=timestamp or _utcnow(),
        severity=severity,
        category=category,
        title=title.strip()[:255],
        description=description,
        status="open",
        origin=origin,
        is_synthetic=is_synthetic,
    )
    session.add(alert)
    session.flush()
    ALERTS_TOTAL.labels(severity=severity).inc()
    log.info("Alert #%s created [%s] %s", alert.id, severity, alert.title)
    _refresh_alert_gauges(session)
    return alert


def create_from_anomaly(session: Session, event: AnomalyEvent) -> SecurityAlert:
    """Raise an alert from a detection event (used by detection_service)."""
    severity = _map_severity(event.severity, event.deviations)
    title = (
        f"{'Critical' if severity == 'critical' else 'Suspicious'} "
        f"activity on {event.device_id}"
    )
    lines = [event.description] if event.description else []
    if event.deviations:
        top = ", ".join(
            f"{k} (|z|={v})" for k, v in list(event.deviations.items())[:3]
        )
        lines.append(f"Top deviating features: {top}.")
    lines.append(
        "Isolation Forest flagged this record as statistically unusual; "
        "this is not a confirmation of attack."
    )
    return create_alert(
        session,
        device_id=event.device_id,
        title=title,
        description="\n".join(lines),
        severity=severity,
        category=_category_for(event.deviations),
        anomaly_id=event.id,
        origin="detection",
        is_synthetic=event.is_synthetic,
        timestamp=event.timestamp,
    )


def _map_severity(detection_severity: str, deviations: dict) -> str:
    if detection_severity == "critical":
        return "critical"
    # connection/authentication anomalies are closer to a security incident
    # than a pure sensor outlier.
    top_two = list(deviations.keys())[:2] if isinstance(deviations, dict) else []
    if "connection_attempts" in top_two or "status_change_rate" in top_two:
        return "high"
    return "medium"


def _category_for(deviations: dict) -> str:
    if not isinstance(deviations, dict) or not deviations:
        return "anomaly"
    top = list(deviations.keys())
    if "connection_attempts" in top[:2]:
        return "connection_anomaly"
    if "message_rate" in top[:2] or "gap_ratio" in top[:2]:
        return "traffic_anomaly"
    if any(k.endswith("_dev") for k in top[:2]):
        return "sensor_anomaly"
    return "anomaly"


def list_alerts(
    session: Session,
    *,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    device_id: Optional[str] = None,
    category: Optional[str] = None,
    since: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0,
) -> AlertList:
    stmt = select(SecurityAlert)
    if status:
        stmt = stmt.where(SecurityAlert.status == status)
    if severity:
        stmt = stmt.where(SecurityAlert.severity == severity)
    if device_id:
        stmt = stmt.where(SecurityAlert.device_id == device_id)
    if category:
        stmt = stmt.where(SecurityAlert.category == category)
    if since:
        stmt = stmt.where(SecurityAlert.timestamp >= since)

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows: Sequence[SecurityAlert] = session.scalars(
        stmt.order_by(SecurityAlert.timestamp.desc())
        .limit(limit).offset(offset)
    ).all()
    return AlertList(
        items=[AlertOut.model_validate(r) for r in rows],
        meta={"total": total, "limit": limit, "offset": offset},
    )


def get_alert(session: Session, alert_id: int) -> Optional[SecurityAlert]:
    return session.get(SecurityAlert, alert_id)


def update_alert(session: Session, alert: SecurityAlert,
                 payload: AlertUpdate) -> SecurityAlert:
    data = payload.model_dump(exclude_unset=True)

    if "status" in data:
        new_status = data["status"]
        current = alert.status
        if new_status == current:
            data.pop("status")
        else:
            if new_status == "acknowledged" and not alert.acknowledged_at:
                alert.acknowledged_at = _utcnow()
            if new_status == "resolved":
                if not alert.resolved_at:
                    alert.resolved_at = _utcnow()
                if not alert.acknowledged_at:
                    alert.acknowledged_at = _utcnow()
            elif current == "resolved" and new_status != "resolved":
                # reopening a resolved alert clears the resolution timestamp
                alert.resolved_at = None
            alert.status = new_status
            log.info("Alert #%s status %s -> %s", alert.id, current, new_status)

    if "assignee" in data and data["assignee"] is not None:
        alert.assignee = data["assignee"]
    if "resolution_note" in data and data["resolution_note"] is not None:
        alert.resolution_note = data["resolution_note"]

    session.flush()
    _refresh_alert_gauges(session)
    return alert


def acknowledge(session: Session, alert_id: int,
                assignee: str = "") -> Optional[SecurityAlert]:
    alert = get_alert(session, alert_id)
    if alert is None:
        return None
    return update_alert(
        session, alert, AlertUpdate(status="acknowledged", assignee=assignee)
    )


def resolve(session: Session, alert_id: int, note: str = "") -> Optional[SecurityAlert]:
    alert = get_alert(session, alert_id)
    if alert is None:
        return None
    return update_alert(
        session, alert, AlertUpdate(status="resolved", resolution_note=note)
    )


def create_manual(session: Session, payload: AlertCreate) -> SecurityAlert:
    return create_alert(
        session,
        device_id=payload.device_id,
        title=payload.title,
        description=payload.description,
        severity=payload.severity,
        category=payload.category,
        origin="manual",
    )


# ------------------------------------------------------------------- counts
def counts(session: Session) -> dict:
    by_status = dict(session.execute(
        select(SecurityAlert.status, func.count())
        .group_by(SecurityAlert.status)
    ).all())
    by_severity = dict(session.execute(
        select(SecurityAlert.severity, func.count())
        .group_by(SecurityAlert.severity)
    ).all())
    unresolved = sum(v for k, v in by_status.items() if k != "resolved")
    return {
        "total": sum(by_status.values()),
        "by_status": {s: by_status.get(s, 0) for s in VALID_STATUSES},
        "by_severity": {s: by_severity.get(s, 0) for s in VALID_SEVERITIES},
        "unresolved": unresolved,
        "open": by_status.get("open", 0),
        "critical": by_severity.get("critical", 0),
    }


def _refresh_alert_gauges(session: Session) -> None:
    try:
        data = counts(session)
        ALERTS_OPEN.set(data["open"] + data["by_status"].get("acknowledged", 0))
        ALERTS_UNRESOLVED.set(data["unresolved"])
    except Exception:  # pragma: no cover - metrics must never break requests
        pass


def recent_events(session: Session, limit: int = 10) -> List[SecurityAlert]:
    return list(session.scalars(
        select(SecurityAlert).order_by(SecurityAlert.timestamp.desc()).limit(limit)
    ).all())
