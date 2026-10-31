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
from app.ml.offline_calibration import (
    binary_metrics,
    calibrate_threshold,
    category_detection_rates,
    stratified_split_indices,
)

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
    artifact_path: str = ""
    artifact_config_path: str = ""


def train(
    *,
    dataset: str = "synthetic",
    csv_path: Optional[str] = None,
    dataset_path: Optional[str] = None,
    contamination: Optional[float] = None,
    threshold: Optional[float] = None,
    label_column: Optional[str] = None,
    limit_rows: Optional[int] = 50_000,
    seed: Optional[int] = None,
    offline_fit_on_benign: Optional[bool] = None,
    calibration_min_precision: Optional[float] = None,
    calibration_max_fpr: Optional[float] = None,
) -> TrainingOutcome:
    """Dispatch to the right training route and return a fitted detector.

    ``dataset='csv'`` remains the generic file mode for backwards
    compatibility; the loader chooses CSV or Parquet from the file extension.
    ``dataset='parquet'`` / ``'file'`` are accepted aliases.
    """
    contamination = contamination if contamination is not None else settings.ml_contamination
    threshold = threshold if threshold is not None else settings.ml_anomaly_threshold
    seed = seed if seed is not None else settings.ml_random_seed
    if limit_rows is None:
        limit_rows = 50_000

    if dataset == "synthetic":
        return _train_stream_synthetic(
            contamination=contamination, threshold=threshold,
            label_column=label_column, seed=seed,
        )
    if dataset in {"csv", "parquet", "file"}:
        path = (dataset_path or "").strip() or (csv_path or "").strip()
        if not path:
            raise ValueError(
                "dataset_path (or csv_path) is required when training from a "
                "file"
            )
        frame, entry = dl.load_frame(
            path, limit_rows=limit_rows, seed=seed,
        )
        if dl.is_telemetry_shaped(frame):
            return _train_stream_frame(
                frame, entry=entry, contamination=contamination,
                threshold=threshold, label_column=label_column, seed=seed,
            )
        return _train_dataset_space(
            frame, entry=entry, contamination=contamination,
            threshold=threshold, label_column=label_column, seed=seed,
            offline_fit_on_benign=offline_fit_on_benign,
            calibration_min_precision=calibration_min_precision,
            calibration_max_fpr=calibration_max_fpr,
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
    offline_fit_on_benign: Optional[bool] = None,
    calibration_min_precision: Optional[float] = None,
    calibration_max_fpr: Optional[float] = None,
) -> TrainingOutcome:
    """Train directly on a public dataset's numeric feature columns.

    When both classes are available, the model is fitted on benign training
    rows only and the alert threshold is selected on a validation split.  This
    matters for attack-heavy datasets such as CICIoT2023: fitting on the full
    sample teaches Isolation Forest that attacks are the normal baseline.  The
    final test split is scored once and never used for fitting or calibration.
    """
    started = time.perf_counter()
    detected = dl.detect_label_column(frame)
    label_column = label_column or detected

    X_df, y, used_columns = dl.prepare_dataset_matrix(
        frame, label_column=label_column
    )
    X = X_df.to_numpy(dtype=np.float32)
    if X.shape[0] < 10:
        raise ValueError("Need at least 10 rows for training")

    fit_on_benign = (
        settings.ml_offline_fit_on_benign
        if offline_fit_on_benign is None
        else bool(offline_fit_on_benign)
    )
    min_precision = (
        settings.ml_offline_calibration_min_precision
        if calibration_min_precision is None
        else float(calibration_min_precision)
    )
    max_fpr = (
        settings.ml_offline_calibration_max_fpr
        if calibration_max_fpr is None
        else float(calibration_max_fpr)
    )

    detector = AnomalyDetector(
        contamination=contamination,
        threshold=threshold,
        flag_quantile=settings.ml_flag_quantile,
        random_seed=seed,
        feature_names=used_columns,
        feature_space="dataset",
    )

    loaded_rows = int(X.shape[0])
    fit_X = X
    metrics: Dict[str, Any] = {}
    measured = False
    eval_note = ""
    categories = _category_values(frame, X_df.index)
    split_metrics: Dict[str, Any] = {}

    if y is not None:
        y_all = y.to_numpy(dtype=int)
        if len(np.unique(y_all)) > 1 and len(y_all) >= 40:
            try:
                train_idx, val_idx, test_idx = stratified_split_indices(
                    y_all, seed=seed,
                )
            except ValueError as exc:
                eval_note = f"Stratified split failed: {exc}"
            else:
                train_benign = y_all[train_idx] == 0
                benign_rows = int(train_benign.sum())
                use_benign_fit = fit_on_benign and benign_rows >= 10
                fit_X = (
                    X[train_idx][train_benign] if use_benign_fit
                    else X[train_idx]
                )
                detector.train(
                    fit_X,
                    dataset_name=entry.name,
                    dataset_kind=entry.kind,
                    dataset_source=entry.download_page or entry.source,
                    metrics={},
                    notes="feature_space=dataset: offline scoring only, not "
                          "usable by the live streaming detector.",
                )
                split_metrics = _dataset_holdout_metrics(
                    detector,
                    X_val=X[val_idx],
                    y_val=y_all[val_idx],
                    X_test=X[test_idx],
                    y_test=y_all[test_idx],
                    categories_test=(
                        categories[test_idx] if categories is not None else None
                    ),
                    manual_threshold=threshold,
                    min_precision=min_precision,
                    max_fpr=max_fpr,
                    fit_on_benign=use_benign_fit,
                    loaded_rows=loaded_rows,
                    fit_rows=int(fit_X.shape[0]),
                )
                metrics = split_metrics["test"]
                measured = "accuracy" in metrics
                eval_note = split_metrics["evaluation_note"]
        else:
            eval_note = "Insufficient label balance for calibration."

    if not split_metrics:
        detector.train(
            fit_X,
            dataset_name=entry.name,
            dataset_kind=entry.kind,
            dataset_source=entry.download_page or entry.source,
            metrics={},
            notes="feature_space=dataset: offline scoring only, not usable by "
                  "the live streaming detector.",
        )

    detector.metrics = {
        **metrics,
        **{k: v for k, v in split_metrics.items() if k != "test"},
        "evaluated_on": "held-out test split (20%)" if measured else "",
        "evaluation_note": eval_note or
            "No usable ground-truth labels; metrics not available.",
    }

    trained_rows = int(detector.training_rows)
    return TrainingOutcome(
        detector=detector,
        version=detector.version,
        training_rows=loaded_rows,
        feature_count=len(used_columns),
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
              "'stream' artifacts. Fitted on benign training rows when labels "
              "are available; threshold selected on a validation split.",
        message=(
            f"Trained dataset-space model on {trained_rows} "
            f"{'benign ' if detector.metrics.get('fit_on_benign') else ''}"
            f"rows (from {loaded_rows} loaded) in "
            f"{time.perf_counter() - started:.2f}s."
        ),
    )


def _dataset_holdout_metrics(
    detector: AnomalyDetector,
    *,
    X_val: np.ndarray,
    y_val: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    categories_test: Optional[np.ndarray],
    manual_threshold: float,
    min_precision: Optional[float],
    max_fpr: Optional[float],
    fit_on_benign: bool,
    loaded_rows: int,
    fit_rows: int,
) -> Dict[str, Any]:
    """Calibrate on validation and measure once on the held-out test split.

    Calibration helpers use attack scores (``-decision_function``), where higher
    means more anomalous.  The detector and its persisted ``threshold`` use raw
    ``decision_function`` scores, where lower means more anomalous, so the
    selected attack threshold is negated before it is stored and used for
    prediction.  Manual thresholds are already in detector space.
    """
    if detector.model is None:
        raise RuntimeError("Detector was not fitted before calibration")

    raw_val = detector.model.decision_function(X_val)
    validation_scores = -raw_val

    if manual_threshold != 0.0:
        detector_threshold = float(manual_threshold)
        attack_threshold = -detector_threshold
        selection = {
            "mode": "manual",
            "threshold": round(detector_threshold, 6),
            "attack_threshold": round(attack_threshold, 6),
            "note": "Manual detector threshold; validation calibration skipped.",
        }
    else:
        try:
            chosen = calibrate_threshold(
                y_val,
                validation_scores,
                min_precision=min_precision,
                max_fpr=max_fpr,
            )
            attack_threshold = float(chosen.threshold)
            detector_threshold = -attack_threshold
            selection = chosen.as_dict()
            selection["attack_threshold"] = round(attack_threshold, 6)
            selection["detector_threshold"] = round(detector_threshold, 6)
        except ValueError as exc:
            detector_threshold = float(detector.threshold)
            attack_threshold = -detector_threshold
            selection = {
                "mode": "fallback_training_quantile",
                "threshold": round(detector_threshold, 6),
                "attack_threshold": round(attack_threshold, 6),
                "note": f"Validation calibration failed: {exc}",
            }

    detector.threshold = detector_threshold
    # Keep the reported severity threshold consistent with the calibrated
    # decision threshold.  A manual/lower threshold must not make every anomaly
    # critical by accident.
    detector.suspicious_level = detector_threshold
    detector.critical_level = min(
        float(detector.critical_level), detector_threshold
    )

    val_preds = (raw_val < detector_threshold).astype(int)
    validation_metrics = binary_metrics(y_val, val_preds, validation_scores)

    raw_test = detector.model.decision_function(X_test)
    test_scores = -raw_test
    test_preds = (raw_test < detector_threshold).astype(int)
    test_metrics = binary_metrics(y_test, test_preds, test_scores)
    test_metrics.update({
        "protocol": (
            "offline hold-out: benign-only fit on the 60% train split, "
            "threshold calibrated on the 20% validation split, metrics on the "
            "untouched 20% test split"
        ),
        "evaluated_on": "held-out test split (20%)",
        "threshold": round(detector_threshold, 6),
        "attack_threshold": round(attack_threshold, 6),
        "fit_rows": fit_rows,
        "loaded_rows": loaded_rows,
        "fit_on_benign": bool(fit_on_benign),
        "threshold_selection": selection,
        "validation": validation_metrics,
    })
    if categories_test is not None:
        test_metrics["attack_categories"] = category_detection_rates(
            categories_test, y_test, test_preds,
        )

    return {
        "test": test_metrics,
        "threshold_selection": selection,
        "fit_rows": fit_rows,
        "loaded_rows": loaded_rows,
        "fit_on_benign": bool(fit_on_benign),
        "validation": validation_metrics,
        "evaluation_note": (
            "Threshold selected on validation only. The test split is scored "
            "once for the reported metrics and was not used for fitting, "
            "preprocessing statistics, or threshold selection."
        ),
    }


def _category_values(
    frame: pd.DataFrame, index: pd.Index,
) -> Optional[np.ndarray]:
    """Return per-row attack categories aligned with the prepared matrix."""
    for column in ("attack_class", "Attack Category", "Label", "label"):
        if column in frame.columns and column != "label":
            return frame.loc[index, column].astype(str).to_numpy()
    return None


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
    """Persist the active (stream-space) artifact and its JSON configuration."""
    path = settings.model_path_absolute
    config = settings.model_config_path_absolute
    outcome.detector.save(path, config)
    outcome.artifact_path = str(path)
    outcome.artifact_config_path = str(config)
    return path


def save_offline_outcome(outcome: TrainingOutcome) -> Path:
    """Persist a dataset-space model to the offline artifacts directory.

    Offline artifacts deliberately live outside the active model path: they
    are never loaded by the live MQTT detector and never replace the active
    stream model.  Each run gets a version-specific ``.joblib`` + ``.json``
    pair so models can be compared later.
    """
    if outcome.feature_space != "dataset":
        raise ValueError(
            "save_offline_outcome only accepts feature_space='dataset' "
            f"outcomes (received '{outcome.feature_space}')"
        )

    directory = settings.offline_model_dir_absolute
    directory.mkdir(parents=True, exist_ok=True)
    safe_version = "".join(
        ch if ch.isalnum() or ch in "-_." else "_" for ch in outcome.version
    ) or "offline-model"
    stem = safe_version
    counter = 1
    while (directory / f"{stem}.joblib").exists() or (
        directory / f"{stem}.json"
    ).exists():
        counter += 1
        stem = f"{safe_version}-{counter}"

    path = directory / f"{stem}.joblib"
    config = directory / f"{stem}.json"
    outcome.detector.save(path, config)
    outcome.artifact_path = str(path)
    outcome.artifact_config_path = str(config)
    log.info(
        "Offline dataset model %s saved to %s (live detector untouched)",
        outcome.version, path,
    )
    return path
