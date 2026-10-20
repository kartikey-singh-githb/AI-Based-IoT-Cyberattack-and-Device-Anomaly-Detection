"""Simulation control and MQTT transport API tests."""

from __future__ import annotations

import time


def _wait_for_messages(client, minimum: int = 1, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    status = {}
    while time.time() < deadline:
        status = client.get("/simulation/status").json()
        if status["simulation"]["messages_published"] >= minimum:
            return status
        time.sleep(0.25)
    return status


def test_status_shape(client):
    body = client.get("/simulation/status").json()
    assert set(body) == {"simulation", "mqtt", "scenarios"}
    assert body["simulation"]["running"] is False
    assert "message_flood" in body["scenarios"]
    assert body["mqtt"]["mode"] in ("fallback", "connected", "disabled")


def test_start_stop_lifecycle(client):
    started = client.post("/simulation/start", json={"device_count": 3})
    assert started.status_code == 200
    body = started.json()
    assert body["running"] is True
    assert body["device_count"] == 3

    again = client.post("/simulation/start", json={"device_count": 2})
    assert again.status_code == 409

    status = _wait_for_messages(client, minimum=1)
    assert status["simulation"]["messages_published"] >= 1

    fleet = client.get("/simulation/devices").json()
    assert fleet["running"] is True
    assert len(fleet["devices"]) == 3
    assert all(d["device_type"] for d in fleet["devices"])

    stopped = client.post("/simulation/stop")
    assert stopped.status_code == 200
    assert stopped.json()["running"] is False
    assert stopped.json()["messages_published"] >= 1

    assert client.post("/simulation/stop").status_code == 409


def test_simulated_data_flows_into_api(client):
    client.post("/simulation/start", json={"device_count": 2})
    try:
        _wait_for_messages(client, minimum=3)
    finally:
        client.post("/simulation/stop")

    devices = client.get("/devices").json()
    assert devices["meta"]["total"] >= 2
    assert all(d["is_simulated"] for d in devices["items"])

    telemetry = client.get("/telemetry").json()
    assert telemetry["meta"]["total"] >= 1
    assert all(t["is_synthetic"] for t in telemetry["items"])
    assert all(t["ingest_source"] != "" for t in telemetry["items"])


def test_inject_requires_running_simulator_or_records_event(client):
    """With the sim stopped, injection still records the labelled event."""
    client.post("/devices", json={
        "device_id": "inject-target", "name": "Target",
        "device_type": "camera", "expected_interval_s": 2.0,
    })
    r = client.post("/simulation/inject", json={
        "device_id": "inject-target",
        "scenario": "telemetry_blackout",
        "messages": 4,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["messages_queued"] == 0
    assert body["anomaly_id"] is not None
    assert body["simulator"]["running"] is False


def test_inject_validation(client):
    client.post("/devices", json={
        "device_id": "inject-target", "name": "Target",
        "device_type": "camera",
    })
    bad = client.post("/simulation/inject", json={
        "device_id": "inject-target", "scenario": "not_a_scenario",
    })
    assert bad.status_code == 422

    too_many = client.post("/simulation/inject", json={
        "device_id": "inject-target", "scenario": "sensor_spike",
        "messages": 5000,
    })
    assert too_many.status_code == 422

    extra = client.post("/simulation/inject", json={
        "device_id": "inject-target", "scenario": "sensor_spike",
        "bogus": True,
    })
    assert extra.status_code == 422


def test_mqtt_status_and_reconnect(client):
    status = client.get("/mqtt/status").json()
    assert "mode" in status and "detail" in status

    forced = client.post("/mqtt/reconnect", json={"force_fallback": True})
    assert forced.status_code == 200
    assert forced.json()["mode"] == "fallback"
    assert forced.json()["mqtt"]["detail"]

    bad = client.post("/mqtt/reconnect", json={"force_fallback": "yes-please"})
    assert bad.status_code == 422


def test_simulation_sessions_recorded(client):
    client.post("/simulation/start", json={"device_count": 2})
    _wait_for_messages(client, minimum=1)
    client.post("/simulation/stop")

    sessions = client.get("/simulation/sessions").json()
    assert len(sessions["items"]) >= 1
    latest = sessions["items"][0]
    assert latest["status"] in ("stopped", "running")
    assert latest["device_count"] == 2
    assert latest["messages_published"] >= 1
