"""End-to-end detection pipeline: ingest -> features -> model -> event/alert."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.db.database import session_scope
from app.db.models import AnomalyEvent
from app.ml import train_model
from app.services import detection_service, model_info_service

_EPOCH = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def trained_model():
    """Train a stream-space model once per test and install it in-process."""
    outcome = train_model.train(dataset="synthetic", label_column="label")
    model_info_service.set_detector(outcome.detector)
    yield outcome
    model_info_service.reset_cache()
    detection_service.reset_runtime()


def _device_payload(device_id: str = "pipeline-device") -> dict:
    return {
        "device_id": device_id,
        "name": "Pipeline device",
        "device_type": "temperature_sensor",
        "status": "online",
        "expected_interval_s": 2.0,
    }


def _post(client, device_id: str, temperature: float, status: str = "online",
          attempts: int = 0, ts: datetime | None = None) -> dict:
    payload = {
        "device_id": device_id,
        "status": status,
        "readings": {
            "temperature": temperature, "humidity": 40.0, "energy_w": 100.0,
            "voltage": 230.0, "current_a": 0.5, "rssi": -60.0,
            "connection_attempts": attempts,
        },
        "is_synthetic": True,
        "ingest_source": "mqtt",
    }
    if ts is not None:
        payload["timestamp"] = ts.isoformat()
    r = client.post("/telemetry", json=payload)
    assert r.status_code == 201
    return r.json()


def _healthy_stream(client, device_id: str, n: int, *,
                    start: datetime | None = None) -> datetime:
    """Post ``n`` well-behaved messages on the device's expected interval.

    Timestamps are spread across the interval rather than wall-clock, so the
    inter-arrival features look like a real device instead of a burst.
    Returns the timestamp after the last message.
    """
    cursor = start or _EPOCH
    for i in range(n):
        _post(client, device_id, 21.0 + (i % 5) * 0.1, ts=cursor)
        cursor = cursor + timedelta(seconds=2.0)
    return cursor


def test_no_model_no_events(client):
    """Without a trained model the pipeline stores data but never invents results."""
    client.post("/devices", json=_device_payload())
    _healthy_stream(client, "pipeline-device", 10)

    anomalies = client.get("/anomalies").json()
    assert anomalies["meta"]["total"] == 0
    assert client.get("/model").json()["trained"] is False


def test_detector_flags_spike_and_creates_alert(client, trained_model):
    client.post("/devices", json=_device_payload())

    # Warm the history with healthy readings, then drive a sensor spike.
    cursor = _healthy_stream(client, "pipeline-device", 20)
    for _ in range(3):
        _post(client, "pipeline-device", 95.0, ts=cursor)
        cursor = cursor + timedelta(seconds=2.0)

    anomalies = client.get("/anomalies").json()
    assert anomalies["meta"]["total"] >= 1
    top = anomalies["items"][0]
    assert top["origin"] == "detector"
    assert top["severity"] in ("suspicious", "critical")
    assert top["is_synthetic"] is True
    assert top["description"].endswith(
        "is not by itself evidence of an attack."
    )
    assert top["alert_id"] is not None
    assert top["deviations"], "expected explanation deviations"

    alert = client.get(f"/alerts/{top['alert_id']}").json()
    assert alert["status"] == "open"
    assert alert["origin"] == "detection"
    assert alert["severity"] in ("medium", "high", "critical")
    assert "not a confirmation of attack" in alert["description"]


def test_healthy_stream_mostly_unflagged(client, trained_model):
    """A normal-looking stream must not be swamped with detections.

    The detector flags the worst ~5% of the training distribution by design,
    so a handful of borderline rows can be flagged; the claim under test is
    that the *bulk* of healthy traffic stays clean and nothing critical
    dominates the feed.
    """
    client.post("/devices", json=_device_payload())
    n = 60
    _healthy_stream(client, "pipeline-device", n)

    anomalies = client.get("/anomalies").json()
    flagged = anomalies["meta"]["total"]
    assert flagged <= n * 0.2, f"{flagged}/{n} healthy rows flagged"

    criticals = client.get("/anomalies",
                           params={"severity": "critical"}).json()
    assert criticals["meta"]["total"] == 0


def test_cooldown_rate_limits_events(client, monkeypatch, trained_model):
    monkeypatch.setattr(settings, "detection_cooldown_s", 3600.0)
    client.post("/devices", json=_device_payload())
    cursor = _healthy_stream(client, "pipeline-device", 15)
    for _ in range(6):
        _post(client, "pipeline-device", 99.0, ts=cursor)
        cursor = cursor + timedelta(seconds=2.0)

    with session_scope() as session:
        count = session.query(AnomalyEvent).filter_by(
            device_id="pipeline-device"
        ).count()
    assert count == 1


def test_scenario_injection_labels_origin(client):
    client.post("/devices", json=_device_payload())
    r = client.post("/simulation/inject", json={
        "device_id": "pipeline-device",
        "scenario": "message_flood",
        "messages": 5,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["anomaly_id"] is not None
    assert body["alert_id"] is not None

    anomaly = client.get(f"/anomalies/{body['anomaly_id']}").json()
    assert anomaly["origin"] == "scenario"
    assert anomaly["is_synthetic"] is True
    assert "generated demo data" in anomaly["description"]

    alert = client.get(f"/alerts/{body['alert_id']}").json()
    assert alert["title"].startswith("[SIMULATED]")
    assert alert["category"] == "simulation_scenario"
    assert alert["origin"] == "scenario"


def test_scenario_injection_unknown_device_404(client):
    r = client.post("/simulation/inject", json={
        "device_id": "missing-device-404",
        "scenario": "sensor_spike",
    })
    assert r.status_code == 404


def test_anomaly_status_update(client):
    client.post("/devices", json=_device_payload())
    client.post("/simulation/inject", json={
        "device_id": "pipeline-device",
        "scenario": "unauthorized_access",
    })
    listed = client.get("/anomalies").json()
    anomaly_id = listed["items"][0]["id"]

    r = client.patch(f"/anomalies/{anomaly_id}", params={"status": "investigating"})
    assert r.status_code == 200
    assert r.json()["status"] == "investigating"

    bad = client.patch(f"/anomalies/{anomaly_id}", params={"status": "banana"})
    assert bad.status_code == 422

    filtered = client.get("/anomalies",
                          params={"status": "investigating"}).json()
    assert filtered["meta"]["total"] == 1
