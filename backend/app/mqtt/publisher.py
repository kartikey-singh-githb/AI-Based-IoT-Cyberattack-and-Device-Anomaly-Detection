"""Publishing helpers used by the simulator and alert pipeline.

All outbound traffic goes through :mod:`app.mqtt.manager` so that the
transport (real broker or labelled fallback) is chosen in one place.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.logging_config import get_logger

log = get_logger("mqtt.publisher")


def build_telemetry_payload(
    *,
    device_id: str,
    readings: Dict[str, Any],
    status: str = "online",
    is_synthetic: bool = True,
    timestamp: Optional[datetime] = None,
) -> Dict[str, Any]:
    """JSON body placed on ``iot/devices/<id>/telemetry``."""
    ts = timestamp or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return {
        "device_id": device_id,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "status": status,
        "readings": readings,
        "is_synthetic": is_synthetic,
        "sent_at": time.time(),
    }


def build_status_payload(
    *, device_id: str, status: str, detail: str = "", is_synthetic: bool = True
) -> Dict[str, Any]:
    """JSON body placed on ``iot/devices/<id>/status``."""
    return {
        "device_id": device_id,
        "status": status,
        "detail": detail,
        "is_synthetic": is_synthetic,
        "sent_at": time.time(),
    }


def publish_telemetry(device_id: str, readings: Dict[str, Any], *,
                      status: str = "online", is_synthetic: bool = True,
                      timestamp: Optional[datetime] = None) -> bool:
    from app.mqtt.manager import mqtt_manager

    payload = build_telemetry_payload(
        device_id=device_id, readings=readings, status=status,
        is_synthetic=is_synthetic, timestamp=timestamp,
    )
    return mqtt_manager.publish(device_id, payload, kind="telemetry")


def publish_status(device_id: str, status: str, detail: str = "") -> bool:
    from app.mqtt.manager import mqtt_manager

    payload = build_status_payload(
        device_id=device_id, status=status, detail=detail
    )
    return mqtt_manager.publish(device_id, payload, kind="status")


def publish_alert(alert: Dict[str, Any]) -> bool:
    from app.mqtt.manager import mqtt_manager

    return mqtt_manager.publish_alert(alert)
