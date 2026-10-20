"""Synthetic telemetry generation and dataset-space training separation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.data.synthetic import (
    CSV_COLUMNS,
    SCENARIOS,
    generate_synthetic_telemetry,
    label_distribution,
    write_sample_csv,
)
from app.ml.anomaly_detector import AnomalyDetector
from app.ml.feature_engineering import extract_dataset_features
from app.services import model_info_service


def test_generation_is_deterministic():
    a = generate_synthetic_telemetry(n_devices=3, rows_per_device=60, seed=99)
    b = generate_synthetic_telemetry(n_devices=3, rows_per_device=60, seed=99)
    pd.testing.assert_frame_equal(a, b)

    c = generate_synthetic_telemetry(n_devices=3, rows_per_device=60, seed=100)
    assert not a["temperature"].equals(c["temperature"])


def test_labels_and_ratio():
    frame = generate_synthetic_telemetry(n_devices=4, rows_per_device=300,
                                         anomaly_ratio=0.08, seed=42)
    assert list(frame.columns) == CSV_COLUMNS
    dist = label_distribution(frame)
    assert "normal" in dist
    assert set(dist) <= {"normal", *SCENARIOS}
    abnormal = sum(v for k, v in dist.items() if k != "normal")
    ratio = abnormal / len(frame)
    assert 0.03 < ratio < 0.13      # near the requested 8%
    assert (frame["source"] == "synthetic").all()


def test_rows_are_plausible():
    frame = generate_synthetic_telemetry(n_devices=2, rows_per_device=100, seed=7)
    normals = frame[frame["label"] == "normal"]
    # Device types measure different channel sets; absent channels are NaN by
    # design, so plausibility is checked only where a value exists.
    temp = normals["temperature"].dropna()
    assert len(temp) > 0
    assert temp.between(-40, 120).all()
    hum = normals["humidity"].dropna()
    assert hum.between(-5, 105).all()
    assert (normals["reading_count"] > 0).all()
    # timestamps strictly increase per device
    for _, group in frame.groupby("device_id"):
        assert group["timestamp"].is_monotonic_increasing


def test_write_and_load_roundtrip(tmp_path):
    path = tmp_path / "sample.csv"
    write_sample_csv(path, n_devices=2, rows_per_device=40, seed=3)
    assert path.exists()
    frame = pd.read_csv(path)
    assert len(frame) == 80
    assert label_distribution(frame)


def test_stream_training_uses_stream_space():
    """The live detector always trains in the stream feature space."""
    from app.ml import train_model

    outcome = train_model.train(dataset="synthetic", label_column="label")
    assert outcome.feature_space == "stream"
    assert outcome.detector.feature_space == "stream"
    assert outcome.metrics_are_measured is True
    # measured on a held-out split, never on the fitted rows alone
    assert set(outcome.metrics) >= {"accuracy", "precision", "recall", "f1"}
    assert 0.0 <= outcome.metrics["accuracy"] <= 1.0


def test_dataset_space_model_refused_for_live_inference(tmp_path):
    """A flow-statistics model must never serve the streaming detector."""
    artifact = tmp_path / "dataset_space.joblib"
    det = AnomalyDetector(
        feature_names=["flow_duration", "tot_fwd_pkts", "tot_bwd_pkts"],
        feature_space="dataset",
        n_estimators=40,
        random_seed=5,
    )
    rng = np.random.default_rng(0)
    det.train(rng.normal(size=(200, 3)))
    det.save(artifact)

    from app.core.config import settings

    saved = (settings.ml_model_path, settings.ml_model_config_path)
    try:
        settings.ml_model_path = str(artifact)
        settings.ml_model_config_path = str(tmp_path / "cfg.json")
        model_info_service.reset_cache()
        assert model_info_service.get_detector() is None
    finally:
        settings.ml_model_path, settings.ml_model_config_path = saved
        model_info_service.reset_cache()


def test_dataset_features_match_stream_space(tmp_path):
    """Offline CSV training must produce the *same* feature names as live."""
    from app.ml import train_model

    frame = generate_synthetic_telemetry(n_devices=3, rows_per_device=80, seed=11)
    path = tmp_path / "frame.csv"
    frame.to_csv(path, index=False)

    outcome = train_model.train(dataset="csv", csv_path=str(path),
                                label_column="label")
    assert outcome.feature_space == "stream"
    assert outcome.detector.feature_names == list(
        extract_dataset_features(frame).columns
    )
