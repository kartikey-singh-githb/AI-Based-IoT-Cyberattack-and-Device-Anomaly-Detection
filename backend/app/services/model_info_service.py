"""Model lifecycle: artifact loading, training records, status reporting.

A single in-process cache holds the active :class:`AnomalyDetector`.  The
cache is refreshed from disk on startup (if an artifact exists) and replaced
immediately after a successful training run.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.metrics import MODEL_TRAINED
from app.db.models import ModelMetadata
from app.ml.anomaly_detector import AnomalyDetector
from app.ml.train_model import TrainingOutcome, save_outcome

log = get_logger("model_info")

_lock = threading.RLock()
_detector: Optional[AnomalyDetector] = None
_load_attempted = False


# --------------------------------------------------------------------------- cache
def set_detector(detector: Optional[AnomalyDetector]) -> None:
    global _detector
    with _lock:
        _detector = detector
    MODEL_TRAINED.set(1 if detector is not None and detector.is_trained else 0)


def get_detector() -> Optional[AnomalyDetector]:
    """Return the cached detector, loading it from disk on first use."""
    global _detector, _load_attempted
    with _lock:
        if _detector is not None:
            return _detector
        if _load_attempted:
            return None
        _load_attempted = True

    path = settings.model_path_absolute
    if not path.exists():
        log.info("No model artifact at %s - detector untrained", path)
        MODEL_TRAINED.set(0)
        return None

    try:
        detector = AnomalyDetector.load(path)
    except Exception as exc:
        log.error("Failed to load model artifact %s: %s", path, exc)
        MODEL_TRAINED.set(0)
        return None

    if detector.feature_space != "stream":
        log.warning(
            "Artifact %s has feature_space='%s'; it cannot serve live "
            "streaming inference and will be ignored by the detector.",
            path, detector.feature_space,
        )
        MODEL_TRAINED.set(0)
        return None

    _detector = detector
    MODEL_TRAINED.set(1)
    log.info("Loaded model %s (%d features)", detector.version,
             len(detector.feature_names))
    return _detector


def reset_cache() -> None:
    """Testing helper: forget the cached detector."""
    global _detector, _load_attempted
    with _lock:
        _detector = None
        _load_attempted = False
    MODEL_TRAINED.set(0)


# ------------------------------------------------------------------- database
def record_training(session: Session, outcome: TrainingOutcome,
                    artifact_path: Path) -> ModelMetadata:
    """Deactivate the previous record and persist the new one."""
    session.execute(
        ModelMetadata.__table__.update().where(
            ModelMetadata.is_active.is_(True)
        ).values(is_active=False)
    )
    row = ModelMetadata(
        version=outcome.version,
        algorithm="IsolationForest",
        trained_at=datetime.now(timezone.utc).replace(tzinfo=None),
        is_active=True,
        training_rows=outcome.training_rows,
        feature_names_json=json.dumps(outcome.feature_names),
        feature_space=outcome.feature_space,
        contamination=outcome.contamination,
        threshold=outcome.threshold,
        random_seed=settings.ml_random_seed,
        dataset_name=outcome.dataset_name,
        dataset_kind=outcome.dataset_kind,
        dataset_source=outcome.dataset_source,
        metrics_json=json.dumps(outcome.metrics, default=str),
        notes=outcome.notes,
    )
    session.add(row)
    session.flush()
    return row


def get_active(session: Session) -> Optional[ModelMetadata]:
    return session.scalars(
        select(ModelMetadata)
        .where(ModelMetadata.is_active.is_(True))
        .order_by(ModelMetadata.trained_at.desc())
        .limit(1)
    ).first()


def list_history(session: Session, limit: int = 20) -> list[ModelMetadata]:
    return list(session.scalars(
        select(ModelMetadata).order_by(ModelMetadata.trained_at.desc()).limit(limit)
    ).all())


def artifact_exists() -> bool:
    return settings.model_path_absolute.exists()


def is_model_available(session: Session) -> bool:
    """True when both the artifact file and an active record exist."""
    if not artifact_exists():
        return False
    detector = get_detector()
    return detector is not None and detector.is_trained


def train_and_activate(session: Session, outcome: TrainingOutcome) -> ModelMetadata:
    """Persist the artifact + metadata and swap the in-process detector."""
    path = save_outcome(outcome)
    row = record_training(session, outcome, path)
    set_detector(outcome.detector)
    log.info("Model %s activated (feature_space=%s)",
             outcome.version, outcome.feature_space)
    return row


# ----------------------------------------------------------------------- info
def model_info(session: Session) -> Dict[str, Any]:
    """Structured model status for the API / dashboard."""
    active = get_active(session)
    detector = get_detector()
    path = settings.model_path_absolute
    exists = artifact_exists()

    if detector is not None:
        info = detector.info()
    elif active is not None:
        info = {
            "trained": True,
            "algorithm": active.algorithm,
            "version": active.version,
            "feature_space": active.feature_space,
            "features": active.feature_names,
            "feature_count": len(active.feature_names),
            "contamination": active.contamination,
            "threshold": active.threshold,
            "flag_quantile": None,
            "critical_level": None,
            "suspicious_level": None,
            "random_seed": active.random_seed,
            "n_estimators": None,
            "trained_at": active.trained_at.isoformat() if active.trained_at else None,
            "training_rows": active.training_rows,
            "training_seconds": None,
            "dataset": {
                "dataset_name": active.dataset_name,
                "dataset_kind": active.dataset_kind,
                "dataset_source": active.dataset_source,
                "notes": active.notes,
            },
            "metrics": active.metrics,
            "baseline": {},
        }
        info["trained"] = exists
    else:
        info = {
            "trained": False,
            "algorithm": "IsolationForest",
            "version": None,
            "feature_space": "stream",
            "features": [],
            "feature_count": 0,
            "contamination": settings.ml_contamination,
            "threshold": settings.ml_anomaly_threshold,
            "flag_quantile": settings.ml_flag_quantile,
            "critical_level": None,
            "suspicious_level": None,
            "random_seed": settings.ml_random_seed,
            "dataset": {},
            "metrics": {},
            "baseline": {},
        }

    metrics = info.get("metrics") or {}
    return {
        "trained": bool(info.get("trained")) and exists,
        "algorithm": info.get("algorithm", "IsolationForest"),
        "version": info.get("version"),
        "trained_at": info.get("trained_at"),
        "training_rows": info.get("training_rows", 0),
        "features": info.get("features", []),
        "feature_count": info.get("feature_count", len(info.get("features", []))),
        "contamination": info.get("contamination", 0.0),
        "threshold": info.get("threshold", 0.0),
        "flag_quantile": info.get("flag_quantile"),
        "random_seed": info.get("random_seed", 0),
        "dataset_name": info.get("dataset", {}).get("dataset_name", ""),
        "dataset_kind": info.get("dataset", {}).get("dataset_kind", ""),
        "dataset_source": info.get("dataset", {}).get("dataset_source", ""),
        "feature_space": info.get("feature_space", "stream"),
        "metrics": metrics,
        "metrics_are_measured": bool(
            metrics.get("accuracy") is not None or metrics.get("roc_auc") is not None
        ),
        "notes": info.get("dataset", {}).get("notes", ""),
        "artifact_path": str(path),
        "artifact_exists": exists,
        "critical_level": info.get("critical_level"),
        "suspicious_level": info.get("suspicious_level"),
        "n_estimators": info.get("n_estimators"),
        "training_seconds": info.get("training_seconds"),
        "baseline": info.get("baseline", {}),
        "caveat": (
            "Isolation Forest detects statistically unusual records relative "
            "to its training baseline. It does not establish malicious intent; "
            "every detection requires human investigation."
        ),
    }
