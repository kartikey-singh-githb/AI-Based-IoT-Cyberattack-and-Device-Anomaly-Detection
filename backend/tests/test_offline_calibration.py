"""Validation-only threshold calibration and offline metric helpers."""

from __future__ import annotations

import numpy as np
import pytest

from app.ml.offline_calibration import (
    binary_metrics,
    calibrate_threshold,
    category_detection_rates,
    stratified_split_indices,
    threshold_policy_comparison,
)


def test_stratified_split_is_disjoint_and_reproducible():
    y = np.array([0] * 30 + [1] * 70)
    first = stratified_split_indices(y, seed=42)
    second = stratified_split_indices(y, seed=42)
    for a, b in zip(first, second):
        np.testing.assert_array_equal(a, b)

    train, val, test = first
    assert len(train) == 60
    assert len(val) == 20
    assert len(test) == 20
    assert not (set(train) & set(val))
    assert not (set(train) & set(test))
    assert not (set(val) & set(test))
    assert set(np.concatenate(first)) == set(range(len(y)))


def test_threshold_calibration_uses_validation_labels_only():
    y_val = np.array([0, 0, 0, 0, 1, 1, 1, 1])
    scores_val = np.array([0.0, 1.0, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0])
    selection = calibrate_threshold(
        y_val, scores_val, min_precision=0.99, max_fpr=0.0,
    )

    assert selection.constrained is True
    assert selection.min_precision == 0.99
    assert selection.max_fpr == 0.0
    assert selection.threshold >= 3.0
    assert selection.validation_metrics["precision"] == 1.0
    assert selection.validation_metrics["false_positive_rate"] == 0.0
    assert "validation split only" in selection.note


def test_impossible_constraints_fall_back_to_unconstrained_f1():
    y_val = np.array([0, 1, 0, 1])
    scores_val = np.array([0.0, 0.0, 1.0, 1.0])
    selection = calibrate_threshold(
        y_val, scores_val, min_precision=0.99, max_fpr=0.0,
    )

    assert selection.constrained is False
    assert "fell back" in selection.note
    assert np.isfinite(selection.threshold)


def test_binary_metrics_and_category_detection_rates():
    y = np.array([0, 0, 1, 1, 1])
    preds = np.array([0, 1, 1, 1, 0])
    scores = np.array([0.1, 0.2, 0.8, 0.9, 0.3])
    metrics = binary_metrics(y, preds, scores)

    assert metrics["confusion_matrix"] == {"tn": 1, "fp": 1, "fn": 1, "tp": 2}
    assert metrics["false_positive_rate"] == 0.5
    assert metrics["false_negative_rate"] == pytest.approx(1 / 3, abs=1e-6)
    assert 0.0 <= metrics["roc_auc"] <= 1.0
    assert 0.0 <= metrics["pr_auc"] <= 1.0

    categories = np.array(["Benign", "Benign", "DDoS", "Recon", "DDoS"])
    rates = category_detection_rates(categories, y, preds)
    assert rates["DDoS"]["detection_rate"] == 0.5
    assert rates["Recon"]["detection_rate"] == 1.0
    assert rates["Benign"]["detection_rate"] == 0.5


def test_threshold_policy_comparison_selects_on_validation():
    y_val = np.array([0, 0, 0, 1, 1, 1])
    val_scores = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    y_test = np.array([0, 0, 0, 1, 1, 1])
    test_scores = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    policies = [{"name": "balanced", "min_precision": 0.5}]

    result = threshold_policy_comparison(
        y_val, val_scores, y_test, test_scores, policies=policies,
    )
    selected = calibrate_threshold(y_val, val_scores, min_precision=0.5)
    assert result["balanced"]["threshold"] == pytest.approx(
        selected.threshold, abs=1e-6
    )
    assert "validation" in result["balanced"]
