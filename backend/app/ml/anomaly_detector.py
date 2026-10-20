"""Isolation Forest anomaly detector wrapper.

Responsibilities
----------------
* Train an IsolationForest on a numeric feature matrix.
* Validate and coerce incoming feature dicts before inference.
* Produce an **anomaly score** (IsolationForest ``decision_function``) and an
  **anomaly flag** derived from a configurable threshold.
* Classify detections as ``normal`` / ``suspicious`` / ``critical`` using
  quantile bands measured on the training data - not arbitrary constants.
* Persist the estimator together with its configuration via joblib.

Interpretation caveat (also documented in docs/ml_methodology.md)
-----------------------------------------------------------------
Isolation Forest identifies records that are *statistically unusual* with
respect to the baseline it was trained on.  An unusual record is not, by
itself, evidence of malicious intent: it may equally be a sensor fault, a
firmware change, a maintenance window or a shift in legitimate behaviour.
Every detection is therefore treated as a lead requiring investigation, and
is labelled "suspicious" or "critical" rather than "attack confirmed".
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from app.core.logging_config import get_logger
from app.ml.feature_engineering import FEATURE_NAMES

log = get_logger("anomaly_detector")

ALGORITHM = "IsolationForest"


@dataclass
class DetectionResult:
    """Outcome of scoring a single feature vector."""

    score: float
    is_anomaly: bool
    severity: str                    # normal | suspicious | critical
    threshold: float
    feature_count: int
    invalid_features: List[str] = field(default_factory=list)
    elapsed_ms: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "score": round(self.score, 6),
            "is_anomaly": self.is_anomaly,
            "severity": self.severity,
            "threshold": round(self.threshold, 6),
            "feature_count": self.feature_count,
            "invalid_features": self.invalid_features,
            "elapsed_ms": round(self.elapsed_ms, 4),
        }


class AnomalyDetector:
    """Thin, dependency-free wrapper around :class:`IsolationForest`."""

    def __init__(
        self,
        *,
        contamination: float = 0.05,
        threshold: float = 0.0,
        flag_quantile: float = 0.05,
        random_seed: int = 42,
        feature_names: Optional[Sequence[str]] = None,
        feature_space: str = "stream",
        version: str = "",
        n_estimators: int = 300,
    ) -> None:
        self.contamination = float(contamination)
        self.threshold = float(threshold)
        self.flag_quantile = float(flag_quantile)
        self.random_seed = int(random_seed)
        self.feature_names: List[str] = list(
            feature_names or FEATURE_NAMES
        )
        self.feature_space = feature_space
        self.version = version or _new_version()
        self.n_estimators = int(n_estimators)

        self.model: Optional[IsolationForest] = None
        self.critical_level: float = -1.0   # 1st-percentile training score
        self.suspicious_level: float = -1.0 # 10th-percentile training score
        self.baseline: Dict[str, Dict[str, float]] = {}
        self.trained_at: Optional[str] = None
        self.training_rows: int = 0
        self.training_seconds: float = 0.0
        self.dataset_info: Dict[str, str] = {}
        self.metrics: Dict[str, Any] = {}

    # ------------------------------------------------------------------ train
    def train(
        self,
        X: np.ndarray,
        *,
        dataset_name: str = "synthetic",
        dataset_kind: str = "synthetic",
        dataset_source: str = "",
        metrics: Optional[Dict[str, Any]] = None,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Fit the model and derive the calibrated severity bands."""
        X = self._validate_matrix(X, fitting=True)
        if X.shape[0] < 10:
            raise ValueError(
                f"Need at least 10 training rows, received {X.shape[0]}"
            )

        started = time.perf_counter()
        self.model = IsolationForest(
            n_estimators=self.n_estimators,
            contamination=self.contamination,
            max_samples="auto",
            random_state=self.random_seed,
            n_jobs=-1,
        )
        self.model.fit(X)
        self.training_seconds = time.perf_counter() - started
        self.training_rows = int(X.shape[0])
        self.trained_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        raw_scores = self.model.decision_function(X)

        # Calibrate the inference threshold and the severity bands from the
        # *training* score distribution.  `threshold` reproduces the configured
        # flag quantile, so the expected alert rate is predictable; 0.0 means
        # "auto-calibrate" and a non-zero value is honoured as a manual
        # override.  Bands are measured, never invented.
        quantile = min(max(self.flag_quantile, 0.001), 0.49)
        self.suspicious_level = float(np.quantile(raw_scores, quantile))
        self.critical_level = float(np.quantile(raw_scores, quantile * 0.25))
        if self.threshold == 0.0:
            self.threshold = self.suspicious_level
        # Baselines for human-readable explanations.
        self.baseline = {}
        for idx, name in enumerate(self.feature_names):
            col = X[:, idx]
            self.baseline[name] = {
                "mean": float(np.mean(col)),
                "std": float(np.std(col)),
            }

        self.dataset_info = {
            "dataset_name": dataset_name,
            "dataset_kind": dataset_kind,
            "dataset_source": dataset_source,
            "notes": notes,
        }
        self.metrics = metrics or {}

        log.info(
            "IsolationForest trained: rows=%d features=%d in %.2fs (v=%s)",
            self.training_rows, X.shape[1], self.training_seconds, self.version,
        )
        return {
            "version": self.version,
            "training_rows": self.training_rows,
            "feature_count": len(self.feature_names),
            "training_seconds": round(self.training_seconds, 3),
            "threshold": self.threshold,
            "flag_quantile": self.flag_quantile,
            "contamination": self.contamination,
            "suspicious_level": round(self.suspicious_level, 6),
            "critical_level": round(self.critical_level, 6),
            "dataset": self.dataset_info,
            "metrics": self.metrics,
        }

    # ---------------------------------------------------------------- predict
    def score_matrix(self, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        if self.model is None:
            raise RuntimeError("Model is not trained")
        X = self._validate_matrix(X, fitting=False)
        scores = self.model.decision_function(X)
        flags = scores < self.threshold
        return scores, flags

    def score_features(self, features: Dict[str, float]) -> DetectionResult:
        """Score a single streaming feature dict."""
        started = time.perf_counter()
        vector, invalid = self._features_to_vector(features)
        if self.model is None:
            raise RuntimeError("Model is not trained")
        X = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        score = float(self.model.decision_function(X)[0])
        elapsed = (time.perf_counter() - started) * 1000.0
        is_anomaly = bool(score < self.threshold)
        return DetectionResult(
            score=score,
            is_anomaly=is_anomaly,
            severity=self.classify_severity(score),
            threshold=self.threshold,
            feature_count=len(self.feature_names),
            invalid_features=invalid,
            elapsed_ms=elapsed,
        )

    def classify_severity(self, score: float) -> str:
        if score >= self.threshold:
            return "normal"
        if score > self.critical_level:
            return "suspicious"
        return "critical"

    @property
    def is_trained(self) -> bool:
        return self.model is not None

    # ------------------------------------------------------------- serialise
    def save(self, path: Path, config_path: Optional[Path] = None) -> None:
        if self.model is None:
            raise RuntimeError("Nothing to save - model is not trained")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model": self.model,
            "feature_names": self.feature_names,
            "feature_space": self.feature_space,
            "contamination": self.contamination,
            "threshold": self.threshold,
            "flag_quantile": self.flag_quantile,
            "random_seed": self.random_seed,
            "version": self.version,
            "n_estimators": self.n_estimators,
            "critical_level": self.critical_level,
            "suspicious_level": self.suspicious_level,
            "baseline": self.baseline,
            "trained_at": self.trained_at,
            "training_rows": self.training_rows,
            "training_seconds": self.training_seconds,
            "dataset_info": self.dataset_info,
            "metrics": self.metrics,
        }
        joblib.dump(payload, path)
        log.info("Model artifact saved to %s", path)

        if config_path is not None:
            config_path = Path(config_path)
            config_path.parent.mkdir(parents=True, exist_ok=True)
            summary = {k: v for k, v in payload.items() if k != "model"}
            summary["algorithm"] = ALGORITHM
            config_path.write_text(
                json.dumps(summary, indent=2, default=str), encoding="utf-8"
            )

    @classmethod
    def load(cls, path: Path) -> "AnomalyDetector":
        payload = joblib.load(Path(path))
        det = cls(
            contamination=payload["contamination"],
            threshold=payload["threshold"],
            flag_quantile=payload.get("flag_quantile", 0.05),
            random_seed=payload["random_seed"],
            feature_names=payload["feature_names"],
            feature_space=payload.get("feature_space", "stream"),
            version=payload.get("version", ""),
            n_estimators=payload.get("n_estimators", 300),
        )
        det.model = payload["model"]
        det.critical_level = payload.get("critical_level", -1.0)
        det.suspicious_level = payload.get("suspicious_level", -1.0)
        det.baseline = payload.get("baseline", {})
        det.trained_at = payload.get("trained_at")
        det.training_rows = payload.get("training_rows", 0)
        det.training_seconds = payload.get("training_seconds", 0.0)
        det.dataset_info = payload.get("dataset_info", {})
        det.metrics = payload.get("metrics", {})
        return det

    # -------------------------------------------------------------- internals
    def _features_to_vector(
        self, features: Dict[str, float]
    ) -> Tuple[List[float], List[str]]:
        vector: List[float] = []
        invalid: List[str] = []
        for name in self.feature_names:
            value = features.get(name, 0.0)
            try:
                fvalue = float(value)
            except (TypeError, ValueError):
                invalid.append(name)
                vector.append(0.0)
                continue
            if math.isnan(fvalue) or math.isinf(fvalue):
                invalid.append(name)
                vector.append(0.0)
                continue
            vector.append(fvalue)
        return vector, invalid

    def _validate_matrix(
        self, X: np.ndarray, *, fitting: bool
    ) -> np.ndarray:
        X = np.asarray(X, dtype=np.float32)
        if X.ndim != 2:
            raise ValueError(f"Expected a 2-D matrix, got shape {X.shape}")
        expected = len(self.feature_names)
        if X.shape[1] != expected:
            raise ValueError(
                f"Expected {expected} features ({self.feature_space} space), "
                f"got {X.shape[1]}"
            )
        if fitting and X.shape[0] == 0:
            raise ValueError("Training matrix is empty")
        # IsolationForest cannot consume NaN/inf; replace them so a single
        # malformed reading never crashes inference.
        if not np.isfinite(X).all():
            X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        return X

    def info(self) -> Dict[str, Any]:
        return {
            "trained": self.is_trained,
            "algorithm": ALGORITHM,
            "version": self.version,
            "feature_space": self.feature_space,
            "features": self.feature_names,
            "feature_count": len(self.feature_names),
            "contamination": self.contamination,
            "threshold": self.threshold,
            "flag_quantile": self.flag_quantile,
            "critical_level": self.critical_level,
            "suspicious_level": self.suspicious_level,
            "random_seed": self.random_seed,
            "n_estimators": self.n_estimators,
            "trained_at": self.trained_at,
            "training_rows": self.training_rows,
            "training_seconds": round(self.training_seconds, 3),
            "dataset": self.dataset_info,
            "metrics": self.metrics,
            "baseline": self.baseline,
        }


def _new_version() -> str:
    return datetime.now(timezone.utc).strftime("if-%Y%m%d-%H%M%S")
