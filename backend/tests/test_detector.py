"""AnomalyDetector unit tests: training, calibration, scoring, persistence."""

from __future__ import annotations

import numpy as np
import pytest

from app.ml.anomaly_detector import AnomalyDetector
from app.ml.feature_engineering import FEATURE_NAMES


def _training_matrix(n: int = 400, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(loc=0.0, scale=1.0, size=(n, len(FEATURE_NAMES))).astype(
        np.float32
    )


def test_train_calibrates_threshold_and_bands():
    det = AnomalyDetector(contamination=0.05, flag_quantile=0.05, random_seed=1)
    info = det.train(_training_matrix())

    assert det.is_trained
    assert det.training_rows == 400
    assert info["feature_count"] == len(FEATURE_NAMES)
    # threshold auto-calibrated to the flag quantile of training scores
    assert det.threshold == pytest.approx(det.suspicious_level)
    assert det.critical_level <= det.suspicious_level
    assert det.baseline[FEATURE_NAMES[0]]["std"] > 0


def test_scoring_and_severity_bands():
    det = AnomalyDetector(random_seed=2, n_estimators=100)
    det.train(_training_matrix())

    normal = det.score_features({name: 0.0 for name in FEATURE_NAMES})
    assert normal.severity == "normal"
    assert normal.is_anomaly is False
    assert normal.feature_count == len(FEATURE_NAMES)

    # Far outside the training distribution on many axes -> critical.
    # (A single extreme axis can score as "normal": IsolationForest's path
    # depth saturates, so outlierness must be spread across features.)
    wild = {name: 50.0 for name in FEATURE_NAMES}
    result = det.score_features(wild)
    assert result.is_anomaly is True
    assert result.severity == "critical"
    assert result.score < det.critical_level


def test_classify_severity_boundaries():
    det = AnomalyDetector(random_seed=3)
    det.threshold = -0.1
    det.critical_level = -0.5
    assert det.classify_severity(-0.05) == "normal"
    assert det.classify_severity(-0.2) == "suspicious"
    assert det.classify_severity(-0.9) == "critical"


def test_untrained_scoring_raises():
    det = AnomalyDetector()
    with pytest.raises(RuntimeError):
        det.score_features({name: 0.0 for name in FEATURE_NAMES})
    with pytest.raises(RuntimeError):
        det.save(__import__("pathlib").Path("unused.joblib"))


def test_invalid_features_replaced_and_reported():
    det = AnomalyDetector(random_seed=4, n_estimators=50)
    det.train(_training_matrix(200))
    features = {name: 0.0 for name in FEATURE_NAMES}
    features["temperature"] = float("nan")
    features["humidity"] = "not-a-number"
    result = det.score_features(features)
    assert set(result.invalid_features) == {"temperature", "humidity"}
    assert np.isfinite(result.score)


def test_wrong_feature_count_rejected():
    det = AnomalyDetector(random_seed=5)
    with pytest.raises(ValueError, match="features"):
        det.train(np.zeros((20, 3), dtype=np.float32))


def test_too_few_rows_rejected():
    det = AnomalyDetector(random_seed=6)
    with pytest.raises(ValueError, match="at least 10"):
        det.train(_training_matrix(5))


def test_save_load_roundtrip(tmp_path):
    det = AnomalyDetector(random_seed=8, n_estimators=80, feature_space="stream")
    det.train(_training_matrix(300))
    artifact = tmp_path / "model.joblib"
    config = tmp_path / "model.json"
    det.save(artifact, config)
    assert config.exists()

    loaded = AnomalyDetector.load(artifact)
    assert loaded.is_trained
    assert loaded.version == det.version
    assert loaded.feature_names == det.feature_names
    assert loaded.threshold == pytest.approx(det.threshold)
    assert loaded.critical_level == pytest.approx(det.critical_level)
    assert loaded.feature_space == "stream"

    features = {name: 0.0 for name in FEATURE_NAMES}
    assert loaded.score_features(features).score == pytest.approx(
        det.score_features(features).score
    )


def test_manual_threshold_override_honoured():
    det = AnomalyDetector(threshold=-0.42, random_seed=9, n_estimators=60)
    det.train(_training_matrix(200))
    assert det.threshold == pytest.approx(-0.42)


def test_seed_reproducibility():
    a = AnomalyDetector(random_seed=11, n_estimators=60)
    b = AnomalyDetector(random_seed=11, n_estimators=60)
    a.train(_training_matrix(250, seed=1))
    b.train(_training_matrix(250, seed=1))
    features = {name: 0.5 for name in FEATURE_NAMES}
    assert a.score_features(features).score == pytest.approx(
        b.score_features(features).score
    )
