"""Telemetry ingestion, listing, sanitisation and stats."""

from __future__ import annotations

from datetime import datetime, timezone

from tests.conftest import ingest


def test_ingest_autoregisters_device(client):
    r = ingest(client, "mqtt-thing-1",
               {"temperature": 21.5, "humidity": 40.0, "rssi": -60})
    assert r.status_code == 201
    body = r.json()
    assert body["device_id"] == "mqtt-thing-1"
    assert body["temperature"] == 21.5
    assert body["ingest_source"] == "api"
    assert body["is_synthetic"] is True

    device = client.get("/devices/mqtt-thing-1").json()
    assert device["message_count"] == 1
    assert device["last_seen"] is not None
    assert device["latest_reading"]["temperature"] == 21.5


def test_reading_aliases_promoted(client):
    r = ingest(client, "alias-device",
               {"temp": "23.5", "power": 120, "current": 1.5})
    body = r.json()
    assert body["temperature"] == 23.5
    assert body["energy_w"] == 120.0
    assert body["current_a"] == 1.5


def test_readings_sanitised(client):
    r = ingest(client, "messy-device", {
        "temperature": 20.0,
        "nested": {"a": 1},          # dicts dropped
        "list": [1, 2],              # lists dropped
        "ok_string": "fine",
        "huge_string": "x" * 1000,  # oversized string dropped
        "bool_value": True,
        "nothing": None,
    })
    readings = r.json()["readings"]
    assert readings["temperature"] == 20.0
    assert readings["ok_string"] == "fine"
    assert readings["bool_value"] is True
    assert "nested" not in readings
    assert "list" not in readings
    assert "huge_string" not in readings
    assert "nothing" in readings


def test_unknown_device_without_autoregister(client):
    from app.db.database import session_scope
    from app.schemas.common import TelemetryIn
    from app.services import telemetry_service

    with session_scope() as session:
        try:
            telemetry_service.ingest_telemetry(
                session,
                TelemetryIn(device_id="ghost-device-99", readings={"a": 1}),
                auto_register=False,
            )
        except KeyError:
            pass
        else:  # pragma: no cover - only if the guard regresses
            raise AssertionError("expected KeyError for unknown device")


def test_list_and_latest_and_stats(client):
    for i in range(5):
        ingest(client, "counted-device", {"temperature": 20 + i})
    ingest(client, "other-device", {"temperature": 5})

    rows = client.get("/telemetry", params={"device_id": "counted-device"}).json()
    assert rows["meta"]["total"] == 5
    assert all(r["device_id"] == "counted-device" for r in rows["items"])

    latest = client.get("/telemetry/latest/counted-device").json()
    assert latest["temperature"] == 24.0

    stats = client.get("/telemetry/stats/counted-device").json()
    assert stats["total_messages"] == 5
    assert stats["messages_last_hour"] == 5
    assert stats["source"] == "api"
    assert stats["is_synthetic"] is True

    assert client.get("/telemetry/latest/never-seen-device").status_code == 404


def test_minutes_filter(client):
    ingest(client, "window-device", {"temperature": 1})
    recent = client.get("/telemetry", params={"minutes": 5}).json()
    assert recent["meta"]["total"] >= 1

    r = client.get("/telemetry", params={"minutes": 0})
    assert r.status_code == 422


def test_timestamp_normalisation(client):
    ts = datetime.now(timezone.utc).isoformat()
    r = ingest(client, "ts-device", {"temperature": 1}, timestamp=ts)
    assert r.status_code == 201
    # stored naive-UTC
    assert r.json()["timestamp"].endswith("Z") or "+" not in r.json()["timestamp"]


def test_extra_payload_fields_rejected(client):
    r = client.post("/telemetry", json={"device_id": "x-device", "readings": {},
                                        "bogus": 1})
    assert r.status_code == 422
