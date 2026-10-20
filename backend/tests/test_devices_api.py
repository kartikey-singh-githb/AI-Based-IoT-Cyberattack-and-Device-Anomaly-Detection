"""Device CRUD API tests."""

from __future__ import annotations

DEVICE = {
    "device_id": "test-sensor-01",
    "name": "Bench sensor",
    "device_type": "temperature_sensor",
    "location": "lab",
    "expected_interval_s": 2.0,
}


def test_create_and_get(client):
    created = client.post("/devices", json=DEVICE)
    assert created.status_code == 201
    body = created.json()
    assert body["device_id"] == DEVICE["device_id"]
    assert body["message_count"] == 0

    fetched = client.get("/devices/test-sensor-01")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Bench sensor"


def test_duplicate_rejected(client):
    assert client.post("/devices", json=DEVICE).status_code == 201
    again = client.post("/devices", json=DEVICE)
    assert again.status_code == 409


def test_list_filters_and_pagination(client):
    for i in range(3):
        payload = dict(DEVICE, device_id=f"fleet-{i}", device_type="camera")
        assert client.post("/devices", json=payload).status_code == 201
    other = dict(DEVICE, device_id="fleet-9", device_type="thermostat")
    assert client.post("/devices", json=other).status_code == 201

    cams = client.get("/devices", params={"device_type": "camera"}).json()
    assert cams["meta"]["total"] == 3
    assert all(d["device_type"] == "camera" for d in cams["items"])

    page = client.get("/devices", params={"limit": 2, "offset": 0}).json()
    assert len(page["items"]) == 2
    assert page["meta"]["total"] == 4


def test_update_and_delete(client):
    client.post("/devices", json=DEVICE)
    patched = client.patch("/devices/test-sensor-01",
                           json={"status": "degraded", "name": "Renamed"})
    assert patched.status_code == 200
    assert patched.json()["status"] == "degraded"

    deleted = client.delete("/devices/test-sensor-01")
    assert deleted.status_code == 204
    assert client.get("/devices/test-sensor-01").status_code == 404


def test_validation_errors(client):
    bad = dict(DEVICE, device_id="x")          # too short
    assert client.post("/devices", json=bad).status_code == 422

    bad = dict(DEVICE, device_id="bad id!")    # illegal characters
    assert client.post("/devices", json=bad).status_code == 422

    bad = dict(DEVICE, expected_interval_s=-1)
    assert client.post("/devices", json=bad).status_code == 422

    bad = dict(DEVICE, unexpected="field")     # extra keys forbidden
    assert client.post("/devices", json=bad).status_code == 422

    assert client.get("/devices/no-such-device").status_code == 404
    assert client.patch("/devices/no-such-device",
                        json={"status": "online"}).status_code == 404
    assert client.delete("/devices/no-such-device").status_code == 404
