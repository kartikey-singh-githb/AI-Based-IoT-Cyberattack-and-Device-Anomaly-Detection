"""Runtime anomaly detection pipeline.

Flow for every ingested telemetry message::

    message -> rolling per-device history -> stream feature extraction
            -> IsolationForest score -> flag / severity
            -> (if flagged) AnomalyEvent + SecurityAlert

Guarantees
----------
* Nothing is written when no trained model is available - the API simply
  reports ``model_trained=false`` instead of inventing results.
* Anomaly events are rate-limited per device so a flood cannot exhaust the
  database (:attr:`Settings.detection_cooldown_s`).
* Detections are labelled *suspicious* / *critical*, never "attack
  confirmed"; the alert text says so explicitly.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.metrics import (
    ANOMALIES_TOTAL,
    DETECTION_SECONDS,
    DETECTION_TOTAL,
)
from app.db.models import AnomalyEvent, Device, SecurityAlert, Telemetry
from app.ml.feature_engineering import (
    extract_stream_features,
    top_deviations,
)
from app.schemas.common import AnomalyList, AnomalyOut
from app.services import alert_service, model_info_service

log = get_logger("detection")

_history: Dict[str, Deque[Dict[str, Any]]] = defaultdict(
    lambda: deque(maxlen=settings.detection_history_len)
)
_last_event_at: Dict[str, float] = {}
_lock = threading.RLock()


def reset_runtime() -> None:
    """Clear in-memory history/cooldowns (used by tests and demo reset)."""
    with _lock:
        _history.clear()
        _last_event_at.clear()


def history_length(device_id: str) -> int:
    return len(_history.get(device_id, ()))


# ---------------------------------------------------------------------------
# Online detection
# ---------------------------------------------------------------------------
def analyse_telemetry(session: Session, row: Telemetry, device: Device) -> Optional[AnomalyEvent]:
    """Score one telemetry record and persist an event when it is flagged."""
    detector = model_info_service.get_detector()
    if detector is None or not detector.is_trained:
        return None

    readings = row.readings
    epoch = row.timestamp.replace(tzinfo=timezone.utc).timestamp()

    newest = {
        "ts": epoch,
        "readings": readings,
        "status": row.device_status,
        "readings_json": row.readings_json,
    }

    with _lock:
        history = _history[device.device_id]
        prior = list(history)
        features = extract_stream_features(
            prior,
            newest,
            expected_interval=device.expected_interval_s,
            window_seconds=float(settings.feature_window_size),
        )
        history.append(newest)

    started = time.perf_counter()
    try:
        result = detector.score_features(features)
    except Exception as exc:
        log.error("Scoring failed for device %s: %s", device.device_id, exc)
        return None
    finally:
        DETECTION_SECONDS.observe(time.perf_counter() - started)

    DETECTION_TOTAL.labels(result="anomaly" if result.is_anomaly else "normal").inc()

    if not result.is_anomaly:
        return None

    if _in_cooldown(device.device_id):
        return None

    deviations = top_deviations(features, detector.baseline, top_n=5)
    severity = result.severity

    event = AnomalyEvent(
        device_id=device.device_id,
        timestamp=row.timestamp,
        score=result.score,
        is_anomaly=True,
        severity=severity,
        threshold=result.threshold,
        model_version=detector.version,
        features_json=_dump(features),
        deviations_json=_dump(deviations),
        description=_describe(deviation_keys=list(deviations),
                              severity=severity,
                              score=result.score,
                              threshold=result.threshold),
        status="new",
        origin="detector",
        is_synthetic=bool(device.is_simulated),
    )
    session.add(event)
    session.flush()

    ANOMALIES_TOTAL.labels(severity=severity).inc()
    device.anomaly_count += 1
    _mark_cooldown(device.device_id)

    alert_service.create_from_anomaly(session, event)
    log.info(
        "Anomaly #%s device=%s severity=%s score=%.4f (threshold %.4f)",
        event.id, device.device_id, severity, result.score, result.threshold,
    )
    return event


def _describe(*, deviation_keys: List[str], severity: str,
              score: float, threshold: float) -> str:
    features_txt = ", ".join(deviation_keys[:4]) or "multiple features"
    return (
        f"IsolationForest score {score:.4f} fell below the configured "
        f"threshold {threshold:.4f} ({severity}). Largest deviations: "
        f"{features_txt}. This indicates statistically unusual behaviour "
        f"relative to the training baseline and requires investigation; it "
        f"is not by itself evidence of an attack."
    )


def _in_cooldown(device_id: str) -> bool:
    if settings.detection_cooldown_s <= 0:
        return False
    with _lock:
        last = _last_event_at.get(device_id)
        if last is None:
            return False
        return (time.time() - last) < settings.detection_cooldown_s


def _mark_cooldown(device_id: str) -> None:
    with _lock:
        _last_event_at[device_id] = time.time()


# ---------------------------------------------------------------------------
# Scenario injection (clearly labelled simulated events)
# ---------------------------------------------------------------------------
SCENARIO_TEXT = {
    "message_flood": (
        "Simulated message-rate flood: the device published far more often "
        "than its configured interval."
    ),
    "sensor_spike": (
        "Simulated sensor spike: readings were driven far outside the "
        "device's plausible operating envelope."
    ),
    "connection_storm": (
        "Simulated connection storm: repeated connection/auth attempts were "
        "reported by the device."
    ),
    "telemetry_blackout": (
        "Simulated telemetry blackout: the device stopped publishing for "
        "several expected intervals."
    ),
    "unauthorized_access": (
        "Simulated unauthorised-access indicators: link quality collapsed "
        "while authentication attempts spiked."
    ),
    "credential_bruteforce": (
        "Simulated credential-bruteforce indicators: a burst of "
        "authentication attempts was reported."
    ),
}

SCENARIO_SEVERITY = {
    "message_flood": "high",
    "sensor_spike": "medium",
    "connection_storm": "high",
    "telemetry_blackout": "medium",
    "unauthorized_access": "critical",
    "credential_bruteforce": "critical",
}


def inject_scenario_event(
    session: Session,
    *,
    device_id: str,
    scenario: str,
    description: str = "",
    timestamp: Optional[datetime] = None,
) -> AnomalyEvent:
    """Record an explicitly-labelled simulated detection for a scenario.

    The model's score is used **only if a trained model exists**; otherwise
    ``score`` stays ``None`` and the record says so.  The event is always
    marked ``origin="scenario"`` so it can never be mistaken for an
    independent model detection.
    """
    if scenario not in SCENARIO_TEXT:
        raise ValueError(f"Unknown scenario '{scenario}'")

    detector = model_info_service.get_detector()
    score: Optional[float] = None
    threshold = settings.ml_anomaly_threshold
    # Alert severity uses the medium/high/critical vocabulary of the alert
    # store; the *event* keeps the detector's suspicious/critical vocabulary
    # so both sources are directly comparable in the anomaly feed.
    alert_severity = SCENARIO_SEVERITY[scenario]
    severity = "critical" if alert_severity == "critical" else "suspicious"
    model_version = "untrained"
    deviations: Dict[str, float] = {}
    features: Dict[str, float] = {}

    if detector is not None and detector.is_trained:
        # Score the device's real recent history so the number is genuine.
        recent = _recent_features_for(session, device_id)
        if recent is not None:
            try:
                result = detector.score_features(recent)
                score = result.score
                threshold = result.threshold
                model_version = detector.version
                features = recent
                deviations = top_deviations(recent, detector.baseline, top_n=5)
                if result.severity == "critical":
                    severity = "critical"
                    alert_severity = "critical"
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("Scenario scoring failed: %s", exc)

    text = SCENARIO_TEXT[scenario]
    if description:
        text = f"{text} {description}"
    if score is None:
        text += (
            " No model score is available (the model is not trained), so this "
            "event was produced by the simulation alone."
        )
    else:
        text += (
            f" Model score {score:.4f} against threshold {threshold:.4f}."
        )
    text += " This is generated demo data, not a real security incident."

    device = session.get(Device, device_id)
    event = AnomalyEvent(
        device_id=device_id,
        timestamp=timestamp or _utcnow(),
        score=score,
        is_anomaly=True,
        severity=severity,
        threshold=threshold,
        model_version=model_version,
        features_json=_dump(features),
        deviations_json=_dump(deviations),
        description=text,
        status="new",
        origin="scenario",
        is_synthetic=True,
    )
    session.add(event)
    session.flush()
    ANOMALIES_TOTAL.labels(severity=severity).inc()

    alert_service.create_alert(
        session,
        device_id=device_id,
        title=f"[SIMULATED] {scenario.replace('_', ' ').title()} on {device_id}",
        description=text,
        severity=alert_severity,
        category="simulation_scenario",
        anomaly_id=event.id,
        origin="scenario",
        is_synthetic=True,
        timestamp=event.timestamp,
    )
    if device is not None:
        device.anomaly_count += 1
    log.info("Scenario event injected: %s on %s", scenario, device_id)
    return event


def _recent_features_for(session: Session, device_id: str) -> Optional[Dict[str, float]]:
    """Recompute features from the device's in-memory history."""
    with _lock:
        entries = list(_history.get(device_id, ()))
    if len(entries) < 2:
        return None
    device = session.get(Device, device_id)
    expected = float(device.expected_interval_s) if device is not None else 2.0
    newest = entries[-1]
    prior = entries[:-1]
    return extract_stream_features(
        prior,
        newest,
        expected_interval=expected,
        window_seconds=float(settings.feature_window_size),
    )


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def list_anomalies(
    session: Session,
    *,
    device_id: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    only_anomalies: bool = True,
    since: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0,
) -> AnomalyList:
    stmt = select(AnomalyEvent)
    if only_anomalies:
        stmt = stmt.where(AnomalyEvent.is_anomaly.is_(True))
    if device_id:
        stmt = stmt.where(AnomalyEvent.device_id == device_id)
    if severity:
        stmt = stmt.where(AnomalyEvent.severity == severity)
    if status:
        stmt = stmt.where(AnomalyEvent.status == status)
    if since:
        stmt = stmt.where(AnomalyEvent.timestamp >= since)

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows: Sequence[AnomalyEvent] = session.scalars(
        stmt.order_by(AnomalyEvent.timestamp.desc())
        .limit(limit).offset(offset)
    ).all()

    alert_by_anomaly = {
        alert.anomaly_id: alert.id
        for alert in session.scalars(
            select(SecurityAlert).where(SecurityAlert.anomaly_id.is_not(None))
        ).all()
    } if rows else {}

    items: List[AnomalyOut] = []
    for row in rows:
        out = AnomalyOut(
            id=row.id,
            device_id=row.device_id,
            timestamp=row.timestamp,
            score=row.score,
            is_anomaly=row.is_anomaly,
            severity=row.severity,
            threshold=row.threshold,
            model_version=row.model_version,
            description=row.description,
            status=row.status,
            origin=row.origin or "detector",
            is_synthetic=row.is_synthetic,
            features=row.features,
            deviations=row.deviations,
            alert_id=alert_by_anomaly.get(row.id),
        )
        items.append(out)

    return AnomalyList(
        items=items,
        meta={"total": total, "limit": limit, "offset": offset},
    )


def get_anomaly(session: Session, anomaly_id: int) -> Optional[AnomalyEvent]:
    return session.get(AnomalyEvent, anomaly_id)


def update_status(session: Session, event: AnomalyEvent, status: str) -> AnomalyEvent:
    if status not in ("new", "investigating", "resolved", "false_positive"):
        raise ValueError(f"Invalid anomaly status '{status}'")
    event.status = status
    session.flush()
    return event


def counts(session: Session) -> Dict[str, Any]:
    by_severity = dict(session.execute(
        select(AnomalyEvent.severity, func.count())
        .where(AnomalyEvent.is_anomaly.is_(True))
        .group_by(AnomalyEvent.severity)
    ).all())
    by_status = dict(session.execute(
        select(AnomalyEvent.status, func.count())
        .where(AnomalyEvent.is_anomaly.is_(True))
        .group_by(AnomalyEvent.status)
    ).all())
    return {
        "total": sum(by_severity.values()),
        "by_severity": by_severity,
        "by_status": by_status,
    }


# ---------------------------------------------------------------------------
def _dump(value: Any) -> str:
    import json

    return json.dumps(value, default=str, separators=(",", ":"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
