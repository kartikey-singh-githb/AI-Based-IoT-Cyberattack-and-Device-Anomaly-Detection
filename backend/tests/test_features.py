"""Feature extraction: streaming and batch paths."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.ml.feature_engineering import (
    BASE_FEATURES,
    FEATURE_NAMES,
    extract_dataset_features,
    extract_stream_features,
    features_to_matrix,
    top_deviations,
)

INTERVAL = 2.0


def _msg(ts: float, temp: float = 21.0, status: str = "online",
         attempts: int = 0) -> dict:
    return {
        "ts": ts,
        "readings": {
            "temperature": temp, "humidity": 40.0, "energy_w": 100.0,
            "voltage": 230.0, "current_a": 0.5, "rssi": -60.0,
            "connection_attempts": attempts,
        },
        "status": status,
    }


def _steady_history(n: int = 20, interval: float = INTERVAL) -> list:
    return [_msg(i * interval) for i in range(n)]


def test_steady_stream_has_all_features_and_no_flags():
    history = _steady_history()
    newest = _msg(len(history) * INTERVAL)
    f = extract_stream_features(history, newest, expected_interval=INTERVAL,
                                window_seconds=60.0)
    assert set(f) == set(FEATURE_NAMES)
    assert all(math.isfinite(v) for v in f.values())
    assert f["is_burst"] == 0.0
    assert f["is_stall"] == 0.0
    assert abs(f["last_gap"] - INTERVAL) < 1e-9
    assert abs(f["gap_ratio"] - 1.0) < 1e-9
    assert f["sensor_coverage"] == 1.0
    assert f["status_degraded"] == 0.0


def test_burst_detected():
    history = _steady_history()
    newest = _msg(history[-1]["ts"] + 0.2)   # 10x too fast
    f = extract_stream_features(history, newest, expected_interval=INTERVAL)
    assert f["is_burst"] == 1.0
    assert f["gap_ratio"] < 0.5


def test_stall_and_missing_ratio():
    history = _steady_history(n=5)
    newest = _msg(history[-1]["ts"] + 20.0)  # long silence
    f = extract_stream_features(history, newest, expected_interval=INTERVAL,
                                window_seconds=30.0)
    assert f["is_stall"] == 1.0
    assert f["missing_message_ratio"] > 0.5
    # only the newest of the 5 gaps is a stall
    assert f["trailing_stall_ratio"] == pytest.approx(0.2)


def test_status_flags_and_attempts():
    history = _steady_history()
    newest = _msg(history[-1]["ts"] + INTERVAL, status="degraded", attempts=9)
    f = extract_stream_features(history, newest, expected_interval=INTERVAL)
    assert f["status_degraded"] == 1.0
    assert f["status_changed"] == 1.0
    assert f["connection_attempts"] == 9.0


def test_single_message_history_is_finite():
    f = extract_stream_features([], _msg(0.0), expected_interval=INTERVAL)
    assert all(math.isfinite(v) for v in f.values())
    assert f["reading_count"] == 7.0


def test_features_to_matrix_order():
    f = extract_stream_features([], _msg(0.0))
    m = features_to_matrix(f)
    assert m.shape == (1, len(FEATURE_NAMES))
    assert float(m[0, FEATURE_NAMES.index("temperature")]) == 21.0


def test_dataset_features_preserve_input_order():
    """Regression: rows must align with the input frame (labels depend on it)."""
    rows = []
    for i in range(10):
        rows.append({"device_id": "a", "timestamp": f"2024-01-01T00:00:{i:02d}Z",
                     "temperature": float(i), "humidity": 40.0})
    for i in range(10):
        rows.append({"device_id": "b", "timestamp": f"2024-01-01T00:00:{i:02d}Z",
                     "temperature": float(100 + i), "humidity": 50.0})
    # interleave devices so grouped processing differs from input order
    frame = pd.DataFrame(rows).iloc[
        [i for pair in zip(range(10), range(10, 20)) for i in pair]
    ].reset_index(drop=True)

    out = extract_dataset_features(frame)
    assert len(out) == len(frame)
    assert out["temperature"].tolist() == frame["temperature"].tolist()


def test_dataset_features_handles_missing_columns():
    frame = pd.DataFrame({
        "device_id": ["x"] * 6,
        "timestamp": [f"2024-01-01T00:00:{i:02d}Z" for i in range(6)],
        "power_w": [50.0, 51.0, 49.0, 52.0, 48.0, 50.0],
    })
    out = extract_dataset_features(frame)
    assert len(out) == 6
    assert out["energy_w"].tolist() == frame["power_w"].tolist()
    assert (out["sensor_coverage"] < 1.0).all()


def test_dataset_features_empty_frame():
    out = extract_dataset_features(pd.DataFrame())
    assert list(out.columns) == FEATURE_NAMES
    assert len(out) == 0


def test_top_deviations_ranks_and_limits():
    features = {name: 0.0 for name in FEATURE_NAMES}
    features["temperature"] = 10.0
    features["humidity"] = -5.0
    baseline = {
        "temperature": {"mean": 0.0, "std": 1.0},
        "humidity": {"mean": 0.0, "std": 2.0},
        "voltage": {"mean": 0.0, "std": 0.0},   # zero std skipped
    }
    devs = top_deviations(features, baseline, top_n=2)
    assert list(devs) == ["temperature", "humidity"]
    assert devs["temperature"] == 10.0
    assert devs["humidity"] == 2.5


def test_no_nan_from_extreme_values():
    history = [_msg(0.0, temp=1e12), _msg(2.0, temp=-1e12)]
    newest = _msg(4.0, temp=float("nan"))
    f = extract_stream_features(history, newest, expected_interval=INTERVAL)
    assert all(math.isfinite(v) for v in f.values())


def test_base_features_cover_timing_behaviour():
    # every timing concept has a dedicated feature
    for name in ("message_rate", "last_gap", "gap_ratio", "gap_z",
                 "is_burst", "is_stall", "trailing_burst_ratio",
                 "trailing_stall_ratio", "missing_message_ratio"):
        assert name in BASE_FEATURES
