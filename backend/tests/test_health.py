"""Health, OpenAPI and Prometheus metrics endpoints."""

from __future__ import annotations


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert body["database"] == "ok"
    assert body["model_trained"] is False
    assert body["simulation_running"] is False
    assert body["mqtt_mode"] in ("fallback", "connected", "disabled", "disconnected")


def test_live_and_ready(client):
    assert client.get("/health/live").status_code == 200
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    body = ready.json()
    assert body["status"] == "ready"
    assert body["database"] == "ok"


def test_openapi_contract(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    for expected in (
        "/health", "/devices", "/telemetry", "/anomalies", "/alerts",
        "/analytics/overview", "/model", "/model/train", "/datasets",
        "/simulation/status", "/simulation/start", "/simulation/stop",
        "/simulation/inject", "/mqtt/status",
    ):
        assert expected in paths, f"missing route {expected}"
    # /metrics exists but is intentionally hidden from the schema
    assert client.get("/metrics").status_code == 200


def test_metrics_exposes_prometheus_series(client):
    # Exercise a few paths first so counters are initialised.
    client.get("/health")
    client.post("/telemetry", json={"device_id": "metrics-device",
                                    "readings": {"temperature": 20.0}})

    r = client.get("/metrics")
    assert r.status_code == 200
    text = r.text
    # gauges are always present; counters appear after first observation
    for name in (
        "p310_model_trained",
        "p310_simulation_running",
        "p310_mqtt_fallback_active",
        "p310_devices",
        "p310_http_requests_total",
        "p310_telemetry_messages_total",
    ):
        assert name in text, f"missing metric {name}"
