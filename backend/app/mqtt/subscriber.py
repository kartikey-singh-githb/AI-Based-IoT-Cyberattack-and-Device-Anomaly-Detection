"""Inbound message handling: turn a transport message into stored telemetry.

The subscriber is transport-agnostic; the manager passes it the decoded
payload together with the ``source`` label (``mqtt`` for a real broker,
``loopback`` for the in-process fallback).
"""

from __future__ import annotations

from typing import Any, Dict

from app.core.logging_config import get_logger
from app.db.database import session_scope
from app.schemas.common import TelemetryIn
from app.services import telemetry_service

log = get_logger("mqtt.subscriber")


def handle_message(topic: str, payload: Dict[str, Any], source: str) -> None:
    """Validate and ingest one telemetry/status message."""
    from app.mqtt.manager import parse_topic

    device_id, kind = parse_topic(topic)
    if kind not in ("telemetry", "status") or not device_id:
        return

    try:
        inbound = TelemetryIn.model_validate(
            {
                "device_id": device_id,
                "timestamp": payload.get("timestamp"),
                "status": payload.get("status", "online"),
                "readings": payload.get("readings", {}),
                "is_synthetic": bool(payload.get("is_synthetic", True)),
                "ingest_source": source,
            }
        )
    except Exception as exc:
        log.warning("Bad payload on %s: %s", topic, exc)
        return

    try:
        with session_scope() as session:
            row = telemetry_service.ingest_telemetry(
                session, inbound, topic=topic, run_detection=True
            )
        log.debug("Ingested %s message for %s (row %s)", source,
                  device_id, row.id)
    except Exception as exc:  # pragma: no cover - defensive
        log.error("Failed to ingest %s message: %s", source, exc)
