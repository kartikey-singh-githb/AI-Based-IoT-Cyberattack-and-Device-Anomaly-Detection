"""Model training pipeline for P_310.

Supports two routes:

* ``stream`` space - trained on features computed from telemetry-shaped data
  (the bundled synthetic sample, or any CSV with sensor columns).  This is
  the model the live detector uses.
* ``dataset`` space - trained directly on the numeric columns of a public
  dataset whose schema (flow/packet statistics) the live pipeline cannot
  reproduce.  Such a model is used for offline scoring only and the runtime
  detector refuses to load it for live inference.

Evaluation metrics are reported **only** when a ground-truth label column is
available, and they are computed on a held-out stratified split, never on the
rows the model was fitted to.  The estimator itself is trained on the whole
stream (including the small contaminated fraction of attack rows), which is
exactly what it faces in production, where labels are unavailable.  When no
labels exist, ``metrics`` is empty and ``metrics_are_measured`` is ``False``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split

from app.core.config import settings
from app.core.logging_config import get_logger
from app.data import dataset_loader as dl
from app.data.synthetic import generate_synthetic_telemetry, label_distribution
from app.ml.anomaly_detector import AnomalyDetector
from app.ml.feature_engineering import FEATURE_NAMES, extract_dataset_features

log = get_logger("train_model")


@dataclass
class TrainingOutcome:
    detector: AnomalyDetector
    version: str
    training_rows: int
    feature_count: int
    feature_names: List[str]
    contamination: float
    threshold: float
    feature_space: str
    dataset_name: str
    dataset_kind: str
    dataset_source: str
    metrics: Dict[str, Any] = field(default_factory=dict)
    metrics_are_measured: bool = False
    label_distribution: Dict[str, int] = field(default_factory=dict)
    notes: str = ""
    message: str = ""


def train(
    *,
    dataset: str = "synthetic",
    csv_path: Optional[str] = None,
    contamination: Optional[float] = None,
    threshold: Optional[float] = None,
    label_column: Optional[str] = None,
    limit_rows: Optional[int] = 50_000,
    seed: Optional[int] = None,
) -> TrainingOutcome:
    """Dispatch to the right training route and return a fitted detector."""
    contamination = contamination if contamination is not None else settings.ml_contamination
    threshold = threshold if threshold is not None else settings.ml_anomaly_threshold
    seed = seed if seed is not None else settings.ml_random_seed

    if dataset == "synthetic":
        return _train_stream_synthetic(
            contamination=contamination, threshold=threshold,
            label_column=label_column, seed=seed,
        )
    if dataset == "csv":
        if not csv_path:
            raise ValueError("csv_path is required when dataset='csv'")
        frame, entry = dl.load_frame(csv_path, limit_rows=limit_rows)
        if dl.is_telemetry_shaped(frame):
            return _train_stream_frame(
                frame, entry=entry, contamination=contamination,
                threshold=threshold, label_column=label_column, seed=seed,
            )
        return _train_dataset_space(
            frame, entry=entry, contamination=contamination,
            threshold=threshold, label_column=label_column, seed=seed,
        )
    raise ValueError(f"Unknown dataset mode '{dataset}'")


# ---------------------------------------------------------------------------
def _train_stream_synthetic(*, contamination: float, threshold: float,
                            label_column: Optional[str],
                            seed: int) -> TrainingOutcome:
    frame = generate_synthetic_telemetry(
        n_devices=8, rows_per_device=400, anomaly_ratio=0.08, seed=seed,
    )
    entry = dl.REGISTRY["synthetic_sample"]
    notes = (
        "Trained on generated synthetic telemetry. The labelled stream is used "
        "as-is (attacks included) for fitting, matching production where no "
        "labels are available; labels drive held-out evaluation only."
    )
    return _train_stream_frame(
        frame, entry=entry, contamination=contamination,
        threshold=threshold, label_column=label_column or "label",
        seed=seed, notes=notes,
    )


def _train_stream_frame(
    frame: pd.DataFrame,
    *,
    entry: dl.DatasetEntry,
    contamination: float,
    threshold: float,
    label_column: Optional[str],
    seed: int,
    notes: str = "",
) -> TrainingOutcome:
    """Train in the streaming feature space from a telemetry-shaped frame."""
    started = time.perf_counter()
    labels: Optional[pd.Series] = None
    if label_column and label_column in frame.columns:
        labels = frame[label_column].astype(str)

    # Compute features once over the *whole* sequence so that training and
    # evaluation share the same windowed history (no train/serve skew).
    features_all = extract_dataset_features(
        frame, window_seconds=float(settings.feature_window_size)
    )

    # Isolation Forest is trained on the *whole* observed stream, including
    # the small fraction of rows that later turn out to be attacks.  This
    # mirrors the real deployment, where no labels are available at training
    # time and the traffic is implicitly contaminated; the estimator's
    # `contamination` parameter exists precisely for that setting.  Labels are
    # used only for held-out evaluation, never for fitting.
    fit_rows = features_all
    if fit_rows.shape[0] < 10:
        raise ValueError(
            f"Only {fit_rows.shape[0]} training rows available after "
            "preprocessing - need at least 10."
        )

    X = fit_rows.to_numpy(dtype=np.float32)
    detector = AnomalyDetector(
        contamination=contamination,
        threshold=threshold,
        flag_quantile=settings.ml_flag_quantile,
        random_seed=seed,
        feature_names=FEATURE_NAMES,
        feature_space="stream",
    )

    metrics: Dict[str, Any] = {}
    measured = False
    eval_note = ""

    if labels is not None and labels.nunique() > 1:
        y = _binary_labels(labels)
        if y is not None and len(np.unique(y)) > 1 and len(y) >= 40:
            Xa = features_all.to_numpy(dtype=np.float32)
            metrics, eval_note = _held_out_evaluation(
                Xa, y, contamination=contamination,
                flag_quantile=detector.flag_quantile, seed=seed,
            )
            measured = "accuracy" in metrics

    det_version = detector.train(
        X,
        dataset_name=entry.name,
        dataset_kind=entry.kind,
        dataset_source=entry.download_page or entry.source,
        metrics=metrics,
        notes=notes,
    )
    detector.metrics = {
        **metrics,
        "evaluated_on": "held-out stratified split (25%)" if measured else "",
        "evaluation_note": eval_note or
            "No usable ground-truth labels; metrics not available.",
    }

    outcome = TrainingOutcome(
        detector=detector,
        version=det_version["version"],
        training_rows=det_version["training_rows"],
        feature_count=det_version["feature_count"],
        feature_names=list(detector.feature_names),
        contamination=contamination,
        threshold=detector.threshold,
        feature_space="stream",
        dataset_name=entry.name,
        dataset_kind=entry.kind,
        dataset_source=entry.download_page or entry.source,
        metrics=detector.metrics,
        metrics_are_measured=measured,
        label_distribution=label_distribution(frame) if "label" in frame else {},
        notes=notes,
        message=f"Trained stream model on {det_version['training_rows']} rows "
                f"in {time.perf_counter() - started:.2f}s.",
    )
    return outcome


def _train_dataset_space(
    frame: pd.DataFrame,
    *,
    entry: dl.DatasetEntry,
    contamination: float,
    threshold: float,
    label_column: Optional[str],
    seed: int,
) -> TrainingOutcome:
    """Train directly on a public dataset's numeric feature columns."""
    started = time.perf_counter()
    detected = dl.detect_label_column(frame)
    label_column = label_column or detected

    X_df, y, used_columns = dl.prepare_dataset_matrix(
        frame, label_column=label_column
    )
    X = X_df.to_numpy(dtype=np.float32)
    if X.shape[0] < 10:
        raise ValueError("Need at least 10 rows for training")

    detector = AnomalyDetector(
        contamination=contamination,
        threshold=threshold,
        flag_quantile=settings.ml_flag_quantile,
        random_seed=seed,
        feature_names=used_columns,
        feature_space="dataset",
    )

    metrics: Dict[str, Any] = {}
    measured = False
    eval_note = ""

    if y is not None:
        metrics, eval_note = _held_out_evaluation(
            X, y.to_numpy(dtype=int), contamination=contamination,
            flag_quantile=detector.flag_quantile, seed=seed,
        )
        measured = "accuracy" in metrics

    trained = detector.train(
        X,
        dataset_name=entry.name,
        dataset_kind=entry.kind,
        dataset_source=entry.download_page or entry.source,
        metrics=metrics,
        notes="feature_space=dataset: offline scoring only, not usable by the "
              "live streaming detector.",
    )
    detector.metrics = {
        **metrics,
        "evaluated_on": "held-out stratified split (25%)" if measured else "",
        "evaluation_note": eval_note or
            "No usable ground-truth labels; metrics not available.",
    }

    return TrainingOutcome(
        detector=detector,
        version=trained["version"],
        training_rows=trained["training_rows"],
        feature_count=trained["feature_count"],
        feature_names=list(used_columns),
        contamination=contamination,
        threshold=detector.threshold,
        feature_space="dataset",
        dataset_name=entry.name,
        dataset_kind=entry.kind,
        dataset_source=entry.download_page or entry.source,
        metrics=detector.metrics,
        metrics_are_measured=measured,
        label_distribution=label_distribution(frame) if "label" in frame else {},
        notes="Offline model. The live detector only accepts feature_space="
              "'stream' artifacts.",
        message=f"Trained dataset-space model on {trained['training_rows']} "
                f"rows in {time.perf_counter() - started:.2f}s.",
    )


# ---------------------------------------------------------------------------
def _binary_labels(labels: pd.Series) -> Optional[np.ndarray]:
    lowered = labels.astype(str).str.lower().str.strip()
    normal_tokens = {"normal", "benign", "0", "legit", "legitimate", "false"}
    if lowered.isin(normal_tokens).any() and (~lowered.isin(normal_tokens)).any():
        return (~lowered.isin(normal_tokens)).astype(int).to_numpy()
    # fall back to a binary split when only two distinct labels exist
    if lowered.nunique() == 2:
        mapping = {v: i for i, v in enumerate(sorted(lowered.unique()))}
        return lowered.map(mapping).astype(int).to_numpy()
    return None


def _held_out_evaluation(
    X: np.ndarray,
    y: np.ndarray,
    *,
    contamination: float,
    flag_quantile: float,
    seed: int,
) -> tuple[Dict[str, Any], str]:
    """Unsupervised evaluation: fit on the training split, score held-out data.

    Isolation Forest is unsupervised, so a "positive" means the model flagged
    the record.  The reported metrics therefore measure how well the
    unsupervised detector reproduces the dataset's attack labels on rows it
    never saw while fitting.  The inference threshold is calibrated from the
    training-score quantile exactly as the persisted artifact does, so the
    figures describe the deployed decision rule rather than a 0.5-style
    default.
    """
    if len(np.unique(y)) < 2 or len(y) < 40:
        return {}, "Insufficient label balance for evaluation."

    try:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.25, random_state=seed, stratify=y,
        )
    except ValueError as exc:
        return {}, f"Split failed: {exc}"

    if len(X_train) < 20:
        return {}, "Too few training rows for a held-out evaluation."

    model = IsolationForestForEval(contamination=contamination, seed=seed)
    model.fit(X_train)
    threshold = model.calibrate_threshold(flag_quantile)
    scores = model.decision_scores(X_test)
    preds = (scores < threshold).astype(int)

    cm = confusion_matrix(y_test, preds, labels=[0, 1])
    tn, fp, fn, tp = (int(v) for v in cm.ravel()) if cm.size == 4 else (0, 0, 0, 0)

    metrics: Dict[str, Any] = {
        "protocol": "unsupervised hold-out: fit on the 75% training split, "
                    "evaluate on the held-out 25% split",
        "evaluated_rows": int(len(y_test)),
        "fit_rows": int(len(X_train)),
        "threshold": round(float(threshold), 6),
        "flag_quantile": float(flag_quantile),
        "accuracy": round(float(accuracy_score(y_test, preds)), 4),
        "precision": round(float(precision_score(y_test, preds, zero_division=0)), 4),
        "recall": round(float(recall_score(y_test, preds, zero_division=0)), 4),
        "f1": round(float(f1_score(y_test, preds, zero_division=0)), 4),
        "confusion_matrix": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
        "predicted_positive_rate": round(float(np.mean(preds)), 4),
        "label_positive_rate": round(float(np.mean(y_test)), 4),
    }
    try:
        if len(np.unique(y_test)) > 1:
            metrics["roc_auc"] = round(float(roc_auc_score(y_test, -scores)), 4)
    except ValueError:
        pass

    note = (
        "Held-out metrics from a stratified 75/25 split. The model is fitted "
        "on the training split only; the persisted artifact is subsequently "
        "refitted on the full sequence, so these figures describe the "
        "evaluation split rather than the exact saved estimator."
    )
    return metrics, note


class IsolationForestForEval:
    """Minimal IsolationForest holder used only for evaluation splits."""

    def __init__(self, *, contamination: float, seed: int) -> None:
        from sklearn.ensemble import IsolationForest

        self._model = IsolationForest(
            n_estimators=300,
            contamination=contamination,
            random_state=seed,
            n_jobs=-1,
        )

    def fit(self, X: np.ndarray) -> "IsolationForestForEval":
        X = np.asarray(X, dtype=np.float32)
        self._model.fit(X)
        self._train_scores = self._model.decision_function(X)
        return self

    def calibrate_threshold(self, flag_quantile: float) -> float:
        import numpy as np

        q = min(max(float(flag_quantile), 0.001), 0.49)
        return float(np.quantile(self._train_scores, q))

    def decision_scores(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float32)
        return self._model.decision_function(X)


def save_outcome(outcome: TrainingOutcome) -> Path:
    """Persist the trained artifact and its JSON configuration."""
    path = settings.model_path_absolute
    config = settings.model_config_path_absolute
    outcome.detector.save(path, config)
    return path
