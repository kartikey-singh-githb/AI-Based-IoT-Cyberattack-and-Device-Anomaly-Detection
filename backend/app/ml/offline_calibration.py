"""Threshold calibration and evaluation helpers for offline dataset models.

CICIoT2023 is strongly attack-heavy (about 85% attack rows in the bundled
sample).  Isolation Forest is unsupervised but not label-blind: fitting it on
such a sample teaches it that attack traffic is the normal baseline.  The
offline training route therefore fits on benign rows only when labels are
available, then selects the decision threshold on a separate validation split.
The test split is used once, for the final reported measurement.

These helpers deliberately keep the scoring convention of
:class:`AnomalyDetector`: raw ``decision_function`` scores are lower for
outliers.  The threshold calibration functions take *attack scores*
(``-decision_function``), so higher means more anomalous and standard
precision/recall curves can be used without inverting the estimator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split


@dataclass(frozen=True)
class ThresholdSelection:
    """Result of selecting an alert threshold on validation scores."""

    threshold: float
    strategy: str
    min_precision: Optional[float]
    max_fpr: Optional[float]
    constrained: bool
    validation_metrics: Dict[str, Any]
    note: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "threshold": round(float(self.threshold), 6),
            "strategy": self.strategy,
            "min_precision": self.min_precision,
            "max_fpr": self.max_fpr,
            "constraints_satisfied": self.constrained,
            "validation_metrics": self.validation_metrics,
            "note": self.note,
        }


def stratified_split_indices(
    y: Sequence[int],
    *,
    seed: int = 42,
    test_size: float = 0.20,
    validation_size: float = 0.25,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return reproducible stratified train/validation/test row indices.

    ``validation_size`` is the fraction of the remaining train+validation
    rows, so the defaults produce a 60/20/20 split.  The test indices are
    created first and are never passed to model fitting or threshold
    calibration.
    """
    y = np.asarray(y, dtype=int)
    if len(y) < 10:
        raise ValueError("Need at least 10 labelled rows for a three-way split")
    positions = np.arange(len(y))
    try:
        trainval_idx, test_idx = train_test_split(
            positions,
            test_size=test_size,
            random_state=seed,
            stratify=y,
        )
        train_idx, val_idx = train_test_split(
            trainval_idx,
            test_size=validation_size,
            random_state=seed,
            stratify=y[trainval_idx],
        )
    except ValueError as exc:
        raise ValueError(f"Stratified split failed: {exc}") from exc
    return (
        np.sort(train_idx),
        np.sort(val_idx),
        np.sort(test_idx),
    )


def calibrate_threshold(
    y_true: Sequence[int],
    attack_scores: Sequence[float],
    *,
    strategy: str = "max_f1",
    min_precision: Optional[float] = None,
    max_fpr: Optional[float] = None,
) -> ThresholdSelection:
    """Choose an alert threshold on validation attack scores.

    ``attack_scores`` must be higher-is-more-anomalous (``-decision_function``
    for Isolation Forest).  The default objective maximises F1, with recall as
    the tie-breaker.  Optional precision/FPR constraints prevent the degenerate
    "flag nearly everything" solution that unconstrained F1 favours when the
    validation set is attack-heavy.

    If no candidate satisfies the constraints, the best unconstrained F1
    threshold is returned and ``constrained`` is ``False`` with an explanatory
    note.  The test set must never be passed to this function.
    """
    y = np.asarray(y_true, dtype=int)
    scores = np.asarray(attack_scores, dtype=float)
    if y.ndim != 1 or scores.ndim != 1 or len(y) != len(scores):
        raise ValueError("y_true and attack_scores must be equal-length 1-D arrays")
    if len(y) == 0:
        raise ValueError("Cannot calibrate a threshold on an empty validation set")
    if len(np.unique(y)) < 2:
        raise ValueError("Validation labels must contain both classes")
    if strategy != "max_f1":
        raise ValueError(f"Unsupported calibration strategy '{strategy}'")
    if min_precision is not None and not 0.0 <= float(min_precision) <= 1.0:
        raise ValueError("min_precision must be within [0, 1]")
    if max_fpr is not None and not 0.0 <= float(max_fpr) <= 1.0:
        raise ValueError("max_fpr must be within [0, 1]")

    precision, recall, thresholds = precision_recall_curve(y, scores)
    # precision_recall_curve returns one extra point (the no-threshold point).
    precision = precision[:-1]
    recall = recall[:-1]
    thresholds = np.asarray(thresholds, dtype=float)
    valid = np.isfinite(thresholds)

    positives = int(y.sum())
    negatives = int(len(y) - positives)
    true_positives = recall * positives
    false_positives = np.zeros_like(true_positives)
    nonzero = precision > 0
    false_positives[nonzero] = (
        true_positives[nonzero] / precision[nonzero] - true_positives[nonzero]
    )
    false_positive_rate = (
        false_positives / negatives if negatives > 0 else np.zeros_like(false_positives)
    )

    f1 = 2.0 * precision * recall / (precision + recall + 1e-12)
    constrained = valid.copy()
    if min_precision is not None:
        constrained &= precision >= float(min_precision) - 1e-12
    if max_fpr is not None:
        constrained &= false_positive_rate <= float(max_fpr) + 1e-12

    note = "Selected on the validation split only; the test split was not used."
    if not constrained.any():
        constrained = valid
        constraints_satisfied = False
        note += (
            " No threshold satisfied the requested precision/FPR constraints; "
            "fell back to unconstrained maximum F1."
        )
    else:
        constraints_satisfied = True

    candidates = np.flatnonzero(constrained)
    # Primary key: highest F1. Secondary: highest recall. Final: highest threshold
    # (least permissive) for deterministic, conservative tie-breaking.
    order = np.lexsort((-thresholds[candidates], -recall[candidates], -f1[candidates]))
    best = int(candidates[order[0]])
    threshold = float(thresholds[best])

    validation_preds = (scores >= threshold).astype(int)
    validation_metrics = binary_metrics(
        y, validation_preds, scores, prefix=""
    )
    return ThresholdSelection(
        threshold=threshold,
        strategy=strategy,
        min_precision=min_precision,
        max_fpr=max_fpr,
        constrained=constraints_satisfied,
        validation_metrics=validation_metrics,
        note=note,
    )


def binary_metrics(
    y_true: Sequence[int],
    preds: Sequence[int] | Sequence[bool],
    attack_scores: Optional[Sequence[float]] = None,
    *,
    prefix: str = "",
) -> Dict[str, Any]:
    """Return the measured binary metrics used by the offline report."""
    y = np.asarray(y_true, dtype=int)
    y_hat = np.asarray(preds, dtype=int)
    if len(y) != len(y_hat):
        raise ValueError("y_true and preds must be equal-length arrays")

    cm = confusion_matrix(y, y_hat, labels=[0, 1])
    if cm.size == 4:
        tn, fp, fn, tp = (int(v) for v in cm.ravel())
    else:  # pragma: no cover - labels=[0, 1] always yields a 2x2 matrix
        tn = fp = fn = tp = 0

    metrics: Dict[str, Any] = {
        f"{prefix}evaluated_rows": int(len(y)),
        f"{prefix}label_positive_rate": _round(np.mean(y) if len(y) else 0.0),
        f"{prefix}accuracy": _round(accuracy_score(y, y_hat) if len(y) else 0.0),
        f"{prefix}balanced_accuracy": _round(
            balanced_accuracy_score(y, y_hat) if len(y) else 0.0
        ),
        f"{prefix}precision": _round(
            precision_score(y, y_hat, zero_division=0) if len(y) else 0.0
        ),
        f"{prefix}recall": _round(
            recall_score(y, y_hat, zero_division=0) if len(y) else 0.0
        ),
        f"{prefix}f1": _round(f1_score(y, y_hat, zero_division=0) if len(y) else 0.0),
        f"{prefix}predicted_positive_rate": _round(np.mean(y_hat) if len(y) else 0.0),
        f"{prefix}false_positive_rate": _round(fp / (fp + tn) if (fp + tn) else 0.0),
        f"{prefix}false_negative_rate": _round(fn / (fn + tp) if (fn + tp) else 0.0),
        f"{prefix}confusion_matrix": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
    }

    if attack_scores is not None and len(np.unique(y)) > 1:
        scores = np.asarray(attack_scores, dtype=float)
        try:
            metrics[f"{prefix}roc_auc"] = _round(roc_auc_score(y, scores))
        except ValueError:
            pass
        try:
            metrics[f"{prefix}pr_auc"] = _round(average_precision_score(y, scores))
        except ValueError:
            pass
    return metrics


def category_detection_rates(
    categories: Sequence[str],
    y_true: Sequence[int],
    preds: Sequence[int] | Sequence[bool],
) -> Dict[str, Dict[str, Any]]:
    """Per attack-category detection rates on a held-out split."""
    cats = np.asarray(categories, dtype=object)
    y = np.asarray(y_true, dtype=int)
    y_hat = np.asarray(preds, dtype=int)
    if not (len(cats) == len(y) == len(y_hat)):
        raise ValueError("categories, y_true and preds must be equal length")

    result: Dict[str, Dict[str, Any]] = {}
    for category in sorted({str(c) for c in cats}):
        mask = np.array([str(c) == category for c in cats])
        if not mask.any():
            continue
        rows = int(mask.sum())
        detected = int(y_hat[mask].sum())
        result[category] = {
            "rows": rows,
            "detected": detected,
            "detection_rate": _round(detected / rows if rows else 0.0),
            "label_positive_rate": _round(float(np.mean(y[mask])) if rows else 0.0),
        }
    return result


def threshold_policy_comparison(
    y_validation: Sequence[int],
    validation_scores: Sequence[float],
    y_test: Sequence[int],
    test_scores: Sequence[float],
    *,
    policies: Iterable[Mapping[str, Any]] = (),
) -> Dict[str, Dict[str, Any]]:
    """Select thresholds on validation and evaluate them on a test split.

    Each policy is a mapping with ``name`` and optional ``min_precision`` /
    ``max_fpr`` keys.  Threshold selection never sees ``y_test`` or
    ``test_scores``; they are used only for the returned measurements.
    """
    y_val = np.asarray(y_validation, dtype=int)
    scores_val = np.asarray(validation_scores, dtype=float)
    y_eval = np.asarray(y_test, dtype=int)
    scores_eval = np.asarray(test_scores, dtype=float)
    output: Dict[str, Dict[str, Any]] = {}
    for policy in policies:
        name = str(policy["name"])
        selection = calibrate_threshold(
            y_val,
            scores_val,
            min_precision=policy.get("min_precision"),
            max_fpr=policy.get("max_fpr"),
        )
        preds = (scores_eval >= selection.threshold).astype(int)
        entry = binary_metrics(y_eval, preds, scores_eval)
        entry["threshold"] = round(selection.threshold, 6)
        entry["constraints_satisfied"] = selection.constrained
        entry["validation"] = selection.validation_metrics
        output[name] = entry
    return output


def _round(value: float, digits: int = 6) -> float:
    return round(float(value), digits)
