"""CSV/Parquet loading, label handling and offline dataset-space training."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.core.config import settings
from app.data import dataset_loader as dl
from app.ml import train_model
from app.ml.anomaly_detector import AnomalyDetector
from app.ml.offline_calibration import ThresholdSelection
from app.schemas.common import TrainRequest
from app.services import model_info_service

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CICIOT2023 = PROJECT_ROOT / "datasets" / "ciciot2023_train.parquet"


def _example_frame(rows: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    labels = np.arange(rows) % 2
    return pd.DataFrame(
        {
            "row_id": np.arange(rows),
            "feature_a": rng.normal(size=rows),
            "feature_b": rng.normal(size=rows),
            "feature_c": rng.integers(0, 100, size=rows),
            "constant": np.ones(rows),
            "Label": np.where(labels == 0, "BenignTraffic", "DDoS"),
            "attack_class": np.where(labels == 0, "Benign", "DDoS"),
            "label": labels,
        }
    )


def _write_parquet(path: Path, rows: int = 400, seed: int = 7) -> pd.DataFrame:
    frame = _example_frame(rows=rows, seed=seed)
    frame.to_parquet(path, index=False)
    return frame


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------
def test_blank_dataset_paths_are_normalized_to_none():
    request = TrainRequest(
        dataset="parquet",
        dataset_path="   ",
        csv_path="",
    )
    assert request.dataset_path is None
    assert request.csv_path is None


def test_csv_loading_still_works(tmp_path):
    frame = _example_frame(rows=80)
    path = tmp_path / "sample.csv"
    frame.to_csv(path, index=False)

    loaded, entry = dl.load_frame(str(path))
    assert len(loaded) == 80
    assert list(loaded.columns) == list(frame.columns)
    assert entry.format == "CSV"


def test_parquet_loading_and_reproducible_sampling(tmp_path):
    path = tmp_path / "sample.parquet"
    frame = _write_parquet(path, rows=400)

    first, entry = dl.load_frame(str(path), limit_rows=100, seed=42)
    second, _ = dl.load_frame(str(path), limit_rows=100, seed=42)
    assert entry.format == "PARQUET"
    assert len(first) == 100
    pd.testing.assert_frame_equal(first, second)
    # The sample must not simply be the first 100 rows.
    assert not first["row_id"].equals(
        frame["row_id"].head(100).reset_index(drop=True)
    )


def test_loader_rejects_unsupported_format(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(dl.DatasetError, match="Unsupported dataset format"):
        dl.load_frame(str(path))


def test_loader_reports_missing_absolute_file(tmp_path):
    missing = tmp_path / "does-not-exist.parquet"
    with pytest.raises(dl.DatasetError, match="File not found"):
        dl.load_frame(str(missing))


# ---------------------------------------------------------------------------
# Labels / preprocessing
# ---------------------------------------------------------------------------
def test_label_column_prefers_binary_label_over_multiclass_columns(tmp_path):
    path = tmp_path / "sample.parquet"
    frame = _write_parquet(path)
    loaded, _ = dl.load_frame(str(path))

    assert dl.detect_label_column(loaded) == "label"
    X, y, used = dl.prepare_dataset_matrix(loaded, label_column="label")
    assert y is not None
    assert set(y.unique()) == {0, 1}
    assert used and not ({"Label", "attack_class", "label", "row_id"} & set(used))
    assert list(X.columns) == used
    assert np.isfinite(X.to_numpy(dtype=float)).all()


@pytest.mark.parametrize(
    "values, expected",
    [
        ([0, 1, 0, 1], [0, 1, 0, 1]),
        (["Benign", "Attack", "Normal", "malicious"], [0, 1, 0, 1]),
        (["BenignTraffic", "DDoS", "BenignTraffic", "DDoS"], [0, 1, 0, 1]),
    ],
)
def test_binary_label_conversion(values, expected):
    encoded = dl._encode_binary_labels(pd.Series(values))
    assert encoded.tolist() == expected


def test_infinite_and_missing_features_are_safe():
    frame = pd.DataFrame(
        {
            "a": [1.0, np.inf, -np.inf, 4.0, 5.0],
            "b": [1.0, np.nan, 3.0, 4.0, 5.0],
            "label": [0, 1, 0, 1, 0],
            "Label": ["Benign", "Attack", "Benign", "Attack", "Benign"],
        }
    )
    X, y, used = dl.prepare_dataset_matrix(frame, label_column="label")
    assert used == ["a", "b"]
    assert np.isfinite(X.to_numpy(dtype=float)).all()
    assert y.tolist() == [0, 1, 0, 1, 0]


# ---------------------------------------------------------------------------
# Offline training / live-model protection
# ---------------------------------------------------------------------------
def test_dataset_space_training_saves_offline_artifacts(tmp_path, monkeypatch):
    path = tmp_path / "sample.parquet"
    _write_parquet(path)
    offline_dir = tmp_path / "offline"
    monkeypatch.setattr(settings, "ml_offline_model_dir", str(offline_dir))

    outcome = train_model.train(
        dataset="parquet",
        dataset_path=str(path),
        label_column="label",
        limit_rows=0,
        seed=42,
    )
    assert outcome.feature_space == "dataset"
    assert outcome.detector.feature_space == "dataset"
    assert outcome.metrics_are_measured is True
    assert {"accuracy", "precision", "recall", "f1", "roc_auc"} <= set(
        outcome.metrics
    )
    assert "label" not in outcome.feature_names
    assert "Label" not in outcome.feature_names
    assert "attack_class" not in outcome.feature_names

    artifact = train_model.save_offline_outcome(outcome)
    config = Path(outcome.artifact_config_path)
    assert artifact.parent == offline_dir
    assert artifact.exists()
    assert config.exists()
    assert artifact.stem == config.stem
    assert outcome.version in artifact.name

    loaded = AnomalyDetector.load(artifact)
    assert loaded.feature_space == "dataset"
    assert loaded.feature_names == outcome.feature_names
    assert loaded.threshold == pytest.approx(
        loaded.metrics["threshold"], abs=1e-6
    )
    assert loaded.metrics["attack_threshold"] == pytest.approx(
        -loaded.threshold, abs=1e-6
    )


def test_frontend_parquet_payload_reaches_training_function(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        settings, "ml_offline_model_dir", str(tmp_path / "offline")
    )
    path = tmp_path / "frontend_payload.parquet"
    _write_parquet(path)

    captured = {}
    real_train = train_model.train

    def spy(**kwargs):
        captured.update(kwargs)
        return real_train(**kwargs)

    monkeypatch.setattr(train_model, "train", spy)

    payload = {
        "dataset": "parquet",
        "csv_path": None,
        "dataset_path": str(path),
        "label_column": "label",
        "limit_rows": 0,
    }
    response = client.post("/model/train", json=payload)

    assert response.status_code == 200, response.text
    assert captured["dataset"] == "parquet"
    assert captured["dataset_path"] == str(path)
    assert captured["csv_path"] is None
    assert captured["label_column"] == "label"
    assert captured["limit_rows"] == 0
    body = response.json()
    assert body["feature_space"] == "dataset"
    assert body["offline"] is True


def test_frontend_csv_payload_uses_canonical_dataset_path(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        settings, "ml_offline_model_dir", str(tmp_path / "offline")
    )
    path = tmp_path / "frontend_payload.csv"
    _example_frame(rows=80).to_csv(path, index=False)

    captured = {}
    real_train = train_model.train

    def spy(**kwargs):
        captured.update(kwargs)
        return real_train(**kwargs)

    monkeypatch.setattr(train_model, "train", spy)

    payload = {
        "dataset": "csv",
        "csv_path": str(path),
        "dataset_path": str(path),
        "label_column": "label",
        "limit_rows": 0,
    }
    response = client.post("/model/train", json=payload)

    assert response.status_code == 200, response.text
    assert captured["dataset"] == "csv"
    assert captured["dataset_path"] == str(path)
    assert captured["label_column"] == "label"
    assert captured["limit_rows"] == 0


def test_api_parquet_training_does_not_replace_live_stream_model(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        settings, "ml_offline_model_dir", str(tmp_path / "offline")
    )

    stream = client.post(
        "/model/train",
        json={"dataset": "synthetic", "label_column": "label"},
    )
    assert stream.status_code == 200, stream.text
    active_before = client.get("/model").json()
    detector_before = model_info_service.get_detector()
    stream_artifact = Path(settings.model_path_absolute)
    stream_mtime = stream_artifact.stat().st_mtime_ns

    path = tmp_path / "ciciot_like.parquet"
    _write_parquet(path)

    response = client.post(
        "/model/train",
        json={
            "dataset": "parquet",
            "dataset_path": str(path),
            "label_column": "label",
            "limit_rows": 0,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "trained"
    assert body["feature_space"] == "dataset"
    assert body["offline"] is True
    assert body["training_rows"] == 400
    assert body["metrics_are_measured"] is True
    assert Path(body["artifact_path"]).exists()
    assert Path(body["artifact_config_path"]).exists()

    active_after = client.get("/model").json()
    assert active_after["version"] == active_before["version"]
    assert active_after["feature_space"] == "stream"
    assert active_after["artifact_path"] == active_before["artifact_path"]
    assert model_info_service.get_detector() is detector_before
    assert stream_artifact.stat().st_mtime_ns == stream_mtime

    history = client.get("/model/history").json()["items"]
    active_versions = [row["version"] for row in history if row["is_active"]]
    assert active_versions == [active_before["version"]]


# ---------------------------------------------------------------------------
# Real CICIoT2023 file (optional, skipped when the dataset is absent)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not CICIOT2023.exists(), reason="CICIoT2023 Parquet file not downloaded"
)
def test_ciciot2023_sample_uses_label_target_and_trains_offline(
    tmp_path, monkeypatch
):
    frame, entry = dl.load_frame(str(CICIOT2023), limit_rows=2_000, seed=42)
    assert entry.id == "ciciot2023_train"
    assert {"Label", "attack_class", "label"} <= set(frame.columns)
    assert dl.detect_label_column(frame) == "label"

    X, y, used = dl.prepare_dataset_matrix(frame, label_column="label")
    assert y is not None
    assert set(y.unique()) == {0, 1}
    assert "Label" not in used
    assert "attack_class" not in used
    assert "label" not in used
    assert 0 < len(used) <= 39

    monkeypatch.setattr(
        settings, "ml_offline_model_dir", str(tmp_path / "offline")
    )
    outcome = train_model.train(
        dataset="parquet",
        dataset_path=str(CICIOT2023),
        label_column="label",
        limit_rows=2_000,
        seed=42,
    )
    assert outcome.feature_space == "dataset"
    assert outcome.training_rows == 2_000
    assert outcome.metrics_are_measured is True
    assert "accuracy" in outcome.metrics
    assert outcome.metrics["fit_on_benign"] is True
    assert outcome.metrics["fit_rows"] < outcome.training_rows
    assert outcome.metrics["threshold_selection"]["strategy"] == "max_f1"
    assert "validation" in outcome.metrics
    assert "attack_categories" in outcome.metrics


def test_offline_training_uses_validation_threshold_and_benign_fit(
    tmp_path, monkeypatch
):
    path = tmp_path / "calibrated.parquet"
    _write_parquet(path, rows=400)
    monkeypatch.setattr(
        settings, "ml_offline_model_dir", str(tmp_path / "offline")
    )

    outcome = train_model.train(
        dataset="parquet",
        dataset_path=str(path),
        label_column="label",
        limit_rows=0,
        seed=42,
        calibration_min_precision=0.50,
    )
    metrics = outcome.metrics

    assert outcome.metrics_are_measured is True
    assert metrics["fit_on_benign"] is True
    assert metrics["fit_rows"] < outcome.training_rows
    assert metrics["threshold"] == pytest.approx(outcome.threshold, abs=1e-6)
    assert metrics["attack_threshold"] == pytest.approx(
        -outcome.threshold, abs=1e-6
    )
    assert metrics["threshold_selection"]["constraints_satisfied"] is True
    assert metrics["threshold_selection"]["min_precision"] == 0.50
    assert "test split" in metrics["protocol"]
    assert metrics["validation"]["evaluated_rows"] > 0


def test_calibrated_threshold_is_converted_from_attack_to_detector_space(
    monkeypatch,
):
    class FakeModel:
        def decision_function(self, X):
            return np.asarray(X, dtype=float)[:, 0]

    detector = SimpleNamespace(
        model=FakeModel(),
        threshold=0.0,
        suspicious_level=0.0,
        critical_level=-0.1,
    )
    monkeypatch.setattr(
        train_model,
        "calibrate_threshold",
        lambda *args, **kwargs: ThresholdSelection(
            threshold=2.0,
            strategy="max_f1",
            min_precision=0.95,
            max_fpr=None,
            constrained=True,
            validation_metrics={},
            note="test",
        ),
    )
    X = np.array([[-3.0], [1.0], [-4.0], [2.0]])

    result = train_model._dataset_holdout_metrics(
        detector,
        X_val=X,
        y_val=np.array([0, 0, 1, 1]),
        X_test=X,
        y_test=np.array([1, 0, 1, 0]),
        categories_test=None,
        manual_threshold=0.0,
        min_precision=0.95,
        max_fpr=None,
        fit_on_benign=True,
        loaded_rows=4,
        fit_rows=4,
    )

    # The calibrated attack-score cutoff is +2.0; the equivalent raw
    # decision_function threshold is -2.0.
    assert detector.threshold == pytest.approx(-2.0)
    assert result["test"]["threshold"] == pytest.approx(-2.0)
    assert result["test"]["attack_threshold"] == pytest.approx(2.0)
    assert result["test"]["confusion_matrix"] == {
        "tn": 2, "fp": 0, "fn": 0, "tp": 2,
    }
    assert result["test"]["accuracy"] == 1.0


def test_api_passes_offline_calibration_options(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "ml_offline_model_dir", str(tmp_path / "offline")
    )
    path = tmp_path / "options.parquet"
    _write_parquet(path, rows=400)

    captured = {}
    real_train = train_model.train

    def spy(**kwargs):
        captured.update(kwargs)
        return real_train(**kwargs)

    monkeypatch.setattr(train_model, "train", spy)
    response = client.post(
        "/model/train",
        json={
            "dataset": "parquet",
            "dataset_path": str(path),
            "label_column": "label",
            "limit_rows": 0,
            "offline_fit_on_benign": False,
            "calibration_min_precision": 0.8,
            "calibration_max_fpr": 0.2,
        },
    )

    assert response.status_code == 200, response.text
    assert captured["offline_fit_on_benign"] is False
    assert captured["calibration_min_precision"] == 0.8
    assert captured["calibration_max_fpr"] == 0.2
    assert response.json()["offline"] is True
