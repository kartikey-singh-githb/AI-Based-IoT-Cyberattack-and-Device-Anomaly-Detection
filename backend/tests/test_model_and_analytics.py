"""Model training API, dataset registry and analytics endpoints."""

from __future__ import annotations

import pytest


def test_model_untrained_shape(client):
    body = client.get("/model").json()
    assert body["trained"] is False
    assert body["algorithm"] == "IsolationForest"
    assert body["feature_space"] == "stream"
    assert body["artifact_exists"] is False
    assert "does not establish malicious intent" in body["caveat"]


def test_train_synthetic_and_activate(client):
    r = client.post("/model/train", json={"dataset": "synthetic",
                                          "label_column": "label"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "trained"
    assert body["training_rows"] > 1000
    assert body["feature_count"] > 20
    assert body["metrics_are_measured"] is True
    assert "accuracy" in body["metrics"]
    assert "roc_auc" in body["metrics"]
    assert 0.0 < body["contamination"] < 0.5

    info = client.get("/model").json()
    assert info["trained"] is True
    assert info["version"] == body["version"]
    assert info["metrics_are_measured"] is True
    assert info["artifact_exists"] is True
    assert info["baseline"], "expected calibrated baseline"
    assert info["suspicious_level"] is not None
    assert info["critical_level"] is not None

    history = client.get("/model/history").json()
    assert len(history["items"]) >= 1
    assert history["items"][0]["version"] == body["version"]


def test_training_is_listed_then_superseded(client):
    first = client.post("/model/train", json={"dataset": "synthetic"}).json()
    second = client.post("/model/train", json={"dataset": "synthetic"}).json()
    assert first["version"] != second["version"]
    history = client.get("/model/history").json()
    assert len(history["items"]) >= 2


def test_train_requires_csv_path(client):
    r = client.post("/model/train", json={"dataset": "csv"})
    assert r.status_code == 422


def test_train_rejects_unknown_dataset(client):
    r = client.post("/model/train", json={"dataset": "mnist"})
    assert r.status_code == 422


def test_train_bad_contamination(client):
    r = client.post("/model/train", json={"dataset": "synthetic",
                                          "contamination": 0.9})
    assert r.status_code == 422


def test_csv_training_with_bundled_sample(client):
    r = client.post("/model/train", json={
        "dataset": "csv",
        "csv_path": "synthetic_sample.csv",
        "label_column": "label",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dataset_kind"] == "synthetic"
    assert body["metrics_are_measured"] is True


def test_dataset_registry(client):
    items = client.get("/datasets").json()["items"]
    ids = {d["id"] for d in items}
    assert {"synthetic_sample", "ciciot2023", "mqtt_iot_ids_2020"} <= ids

    synthetic = next(d for d in items if d["id"] == "synthetic_sample")
    assert synthetic["available"] is True
    assert synthetic["rows"] > 0
    assert synthetic["kind"] == "synthetic"

    public = next(d for d in items if d["id"] == "ciciot2023")
    assert public["kind"] == "public"
    assert public["available"] is False       # not downloaded
    assert "unb.ca" in public["download_page"]
    assert public["license"]

    detail = client.get("/datasets/synthetic_sample").json()
    assert detail["id"] == "synthetic_sample"
    assert client.get("/datasets/nope").status_code == 404


def test_generate_sample_endpoint(client):
    r = client.post("/datasets/generate-sample")
    assert r.status_code == 200
    assert r.json()["rows"] > 0


def test_analytics_overview_empty(client):
    body = client.get("/analytics/overview").json()
    assert body["total_devices"] == 0
    assert body["total_telemetry"] == 0
    assert body["model_trained"] is False
    assert body["mqtt_mode"] in ("fallback", "connected", "disabled")


def test_analytics_counts_after_ingest(client):
    client.post("/model/train", json={"dataset": "synthetic"})
    for i in range(5):
        client.post("/telemetry", json={
            "device_id": "stats-device", "status": "online",
            "readings": {"temperature": 21.0 + i * 0.1},
        })
    client.post("/simulation/inject", json={
        "device_id": "stats-device", "scenario": "sensor_spike",
    })

    overview = client.get("/analytics/overview").json()
    assert overview["total_devices"] == 1
    assert overview["total_telemetry"] == 5
    assert overview["total_anomalies"] >= 1
    assert overview["open_alerts"] >= 1
    assert overview["model_trained"] is True

    performance = client.get("/analytics/performance").json()
    assert performance["total_detections"] >= 1
    assert "by_severity" in performance
    assert "note" in performance

    timeline = client.get("/analytics/timeline").json()
    assert len(timeline["items"]) >= 1

    summary = client.get("/analytics/summary").json()
    assert "telemetry_by_hour" in summary
    assert "top_anomaly_devices" in summary


def test_alert_lifecycle(client):
    client.post("/devices", json={
        "device_id": "alert-device", "name": "A", "device_type": "camera",
    })
    created = client.post("/alerts", json={
        "device_id": "alert-device",
        "title": "Manual operator note",
        "severity": "low",
        "category": "manual",
        "description": "checked by hand",
    })
    assert created.status_code == 201
    alert_id = created.json()["id"]

    ack = client.post(f"/alerts/{alert_id}/acknowledge",
                      params={"assignee": "alice"})
    assert ack.status_code == 200
    assert ack.json()["status"] == "acknowledged"
    assert ack.json()["assignee"] == "alice"

    resolved = client.post(f"/alerts/{alert_id}/resolve",
                           params={"note": "sensor was being cleaned"})
    assert resolved.status_code == 200
    body = resolved.json()
    assert body["status"] == "resolved"
    assert body["resolution_note"] == "sensor was being cleaned"

    counts = client.get("/alerts/counts").json()
    assert counts["total"] >= 1

    bad = client.post("/alerts", json={
        "device_id": "alert-device", "title": "x" * 300,
    })
    assert bad.status_code == 422
