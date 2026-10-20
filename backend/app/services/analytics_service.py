"""Aggregated analytics for the dashboard.

Every number here is computed from the SQLite tables - nothing is generated
or approximated.  Buckets use SQLite ``strftime`` on the stored UTC
timestamps, so an empty period simply does not appear in the result and the
frontend renders a zero-length series rather than fabricated points.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.state import app_state
from app.db.models import (
    AnomalyEvent,
    Device,
    SecurityAlert,
    Telemetry,
)
from app.schemas.common import AnalyticsSummary, OverviewStats, TimeseriesPoint
from app.services import alert_service, model_info_service


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hour_bucket(column):
    return func.strftime("%Y-%m-%d %H:00", column)


def overview(session: Session) -> OverviewStats:
    now = _utcnow()
    hour_ago = now - timedelta(hours=1)

    status_rows = session.execute(
        select(Device.status, func.count()).group_by(Device.status)
    ).all()
    status_counts = {s: c for s, c in status_rows}

    total_telemetry = session.scalar(select(func.count()).select_from(Telemetry)) or 0
    telemetry_hour = (
        session.scalar(
            select(func.count()).select_from(Telemetry)
            .where(Telemetry.timestamp >= hour_ago)
        )
        or 0
    )

    total_anomalies = (
        session.scalar(
            select(func.count()).select_from(AnomalyEvent)
            .where(AnomalyEvent.is_anomaly.is_(True))
        )
        or 0
    )
    anomalies_hour = (
        session.scalar(
            select(func.count()).select_from(AnomalyEvent)
            .where(AnomalyEvent.is_anomaly.is_(True),
                   AnomalyEvent.timestamp >= hour_ago)
        )
        or 0
    )

    alerts = alert_service.counts(session)
    mqtt = app_state.mqtt.snapshot()
    sim = app_state.simulation.snapshot()

    return OverviewStats(
        total_devices=sum(status_counts.values()),
        online_devices=status_counts.get("online", 0),
        offline_devices=status_counts.get("offline", 0),
        degraded_devices=status_counts.get("degraded", 0),
        total_telemetry=total_telemetry,
        telemetry_last_hour=telemetry_hour,
        total_anomalies=total_anomalies,
        anomalies_last_hour=anomalies_hour,
        open_alerts=alerts["open"],
        critical_alerts=alerts["critical"],
        unresolved_alerts=alerts["unresolved"],
        mqtt_connected=bool(mqtt["connected"]),
        mqtt_mode=str(mqtt["mode"]),
        model_trained=model_info_service.is_model_available(session),
        simulation_running=bool(sim["running"]),
        generated_at=now,
    )


def summary(session: Session, *, hours: int = 24) -> AnalyticsSummary:
    now = _utcnow()
    since = now - timedelta(hours=hours)

    telemetry_by_hour = [
        TimeseriesPoint(bucket=b, value=float(v))
        for b, v in session.execute(
            select(_hour_bucket(Telemetry.timestamp), func.count())
            .where(Telemetry.timestamp >= since)
            .group_by(_hour_bucket(Telemetry.timestamp))
            .order_by(_hour_bucket(Telemetry.timestamp))
        ).all()
    ]

    anomalies_by_hour = [
        TimeseriesPoint(bucket=b, value=float(v))
        for b, v in session.execute(
            select(_hour_bucket(AnomalyEvent.timestamp), func.count())
            .where(AnomalyEvent.is_anomaly.is_(True),
                   AnomalyEvent.timestamp >= since)
            .group_by(_hour_bucket(AnomalyEvent.timestamp))
            .order_by(_hour_bucket(AnomalyEvent.timestamp))
        ).all()
    ]

    alerts_by_severity = [
        TimeseriesPoint(bucket=b, value=float(v))
        for b, v in session.execute(
            select(SecurityAlert.severity, func.count())
            .group_by(SecurityAlert.severity)
        ).all()
    ]

    device_health = [
        TimeseriesPoint(bucket=b, value=float(v))
        for b, v in session.execute(
            select(Device.status, func.count()).group_by(Device.status)
        ).all()
    ]

    top_anomaly_devices = [
        {"device_id": d, "count": int(c),
         "last_seen": (ts.isoformat() + "Z") if ts else None}
        for d, c, ts in session.execute(
            select(AnomalyEvent.device_id, func.count(), func.max(AnomalyEvent.timestamp))
            .where(AnomalyEvent.is_anomaly.is_(True))
            .group_by(AnomalyEvent.device_id)
            .order_by(func.count().desc())
            .limit(10)
        ).all()
    ]

    telemetry_by_device = [
        {"device_id": d, "count": int(c),
         "anomalies": 0,   # filled from anomaly_counts below
         "last_seen": (ts.isoformat() + "Z") if ts else None}
        for d, c, ts in session.execute(
            select(Telemetry.device_id, func.count(), func.max(Telemetry.timestamp))
            .group_by(Telemetry.device_id)
            .order_by(func.count().desc())
            .limit(20)
        ).all()
    ]
    anomaly_counts = {
        row.device_id: row.count
        for row in session.execute(
            select(AnomalyEvent.device_id, func.count())
            .where(AnomalyEvent.is_anomaly.is_(True))
            .group_by(AnomalyEvent.device_id)
        ).all()
    }
    for entry in telemetry_by_device:
        entry["anomalies"] = int(anomaly_counts.get(entry["device_id"], 0))

    return AnalyticsSummary(
        telemetry_by_hour=telemetry_by_hour,
        anomalies_by_hour=anomalies_by_hour,
        alerts_by_severity=alerts_by_severity,
        device_health=device_health,
        top_anomaly_devices=top_anomaly_devices,
        telemetry_by_device=telemetry_by_device,
        generated_at=now,
    )


def detection_performance(session: Session) -> Dict[str, Any]:
    """Outcome distribution of stored detections.

    This is a *status* breakdown, not a precision/recall claim - measured
    evaluation metrics only appear on the model endpoint when a labelled
    dataset was used for training.
    """
    rows = session.execute(
        select(AnomalyEvent.severity, AnomalyEvent.status, func.count())
        .where(AnomalyEvent.is_anomaly.is_(True))
        .group_by(AnomalyEvent.severity, AnomalyEvent.status)
    ).all()
    by_severity: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    total = 0
    for severity, status, count in rows:
        by_severity[severity] = by_severity.get(severity, 0) + int(count)
        by_status[status] = by_status.get(status, 0) + int(count)
        total += int(count)

    resolved = by_status.get("resolved", 0)
    false_positive = by_status.get("false_positive", 0)
    return {
        "total_detections": total,
        "by_severity": by_severity,
        "by_status": by_status,
        "resolved": resolved,
        "false_positive": false_positive,
        "investigating": by_status.get("investigating", 0),
        "new": by_status.get("new", 0),
        "resolution_rate": round(resolved / total, 4) if total else 0.0,
        "false_positive_rate": round(false_positive / total, 4) if total else 0.0,
        "note": "Status statistics of stored detections. Model quality "
                "metrics (precision/recall/ROC-AUC) are reported on the "
                "model endpoint only when trained with labelled data.",
    }


def timeline(session: Session, *, hours: int = 6, limit: int = 200
             ) -> List[Dict[str, Any]]:
    """Merged activity feed of alerts for the overview page."""
    since = _utcnow() - timedelta(hours=hours)
    alerts = session.scalars(
        select(SecurityAlert)
        .where(SecurityAlert.timestamp >= since)
        .order_by(SecurityAlert.timestamp.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": a.id,
            "type": "alert",
            "device_id": a.device_id,
            "title": a.title,
            "severity": a.severity,
            "status": a.status,
            "category": a.category,
            "origin": a.origin,
            "is_synthetic": a.is_synthetic,
            "timestamp": a.timestamp.isoformat() + "Z",
            "anomaly_id": a.anomaly_id,
        }
        for a in alerts
    ]
