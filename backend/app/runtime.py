"""Background services owned by the API process.

``main.py``'s lifespan calls :func:`start_background_services` /
:func:`stop_background_services`.  Nothing here is required for the HTTP API
to work: every component degrades gracefully (MQTT falls back to the labelled
in-process transport, the simulator simply stays stopped if not requested).
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from app.core.config import settings
from app.core.logging_config import get_logger
from app.mqtt.manager import mqtt_manager
from app.services import detection_service
from app.simulator.device_simulator import simulator

log = get_logger("runtime")

_lock = threading.RLock()
_started = False


def start_background_services() -> Dict[str, Any]:
    """Bring up MQTT (or its fallback) and, if configured, the simulator."""
    global _started
    with _lock:
        mqtt_mode = mqtt_manager.start()
        sim_status: Dict[str, Any] = {"running": False, "reason": "not requested"}
        if settings.sim_auto_start:
            try:
                sim_status = simulator.start()
            except Exception as exc:  # pragma: no cover - defensive
                log.error("Simulator failed to start: %s", exc)
                sim_status = {"running": False, "reason": str(exc)}
        _started = True
        log.info("Background services up (mqtt=%s, simulation=%s)",
                 mqtt_mode, sim_status.get("running"))
        return {"mqtt": mqtt_mode, "simulation": sim_status}


def stop_background_services() -> None:
    global _started
    with _lock:
        try:
            simulator.stop()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Simulator stop failed: %s", exc)
        try:
            mqtt_manager.stop()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("MQTT stop failed: %s", exc)
        _started = False
        log.info("Background services stopped")


def status() -> Dict[str, Any]:
    return {
        "started": _started,
        "mqtt": mqtt_manager.status(),
        "simulation": simulator.status(),
    }


# ---------------------------------------------------------------------------
# Scenario injection (API entry point)
# ---------------------------------------------------------------------------
def inject_scenario(
    *,
    device_id: str,
    scenario: str,
    messages: Optional[int] = None,
    description: str = "",
) -> Dict[str, Any]:
    """Inject an attack scenario into the live pipeline.

    Two things happen, deliberately:

    1. The simulator is asked to emit abnormal telemetry for that device, so
       the *model* has a real chance to detect the traffic itself.
    2. A clearly labelled scenario event/alert (``origin="scenario"``) is
       recorded, so a demo never depends on the model happening to flag it.
    """
    queued: Dict[str, Any] = {}
    sim_error: Optional[str] = None
    if simulator.running:
        try:
            queued = simulator.inject(device_id, scenario, messages)
        except KeyError as exc:
            sim_error = str(exc)
            queued = {}
    else:
        sim_error = "Simulator is not running; only the labelled scenario event was recorded."

    from app.db.database import session_scope

    with session_scope() as session:
        from app.db.models import Device, SecurityAlert

        if session.get(Device, device_id) is None:
            raise KeyError(f"Unknown device '{device_id}'")

        event = detection_service.inject_scenario_event(
            session, device_id=device_id, scenario=scenario,
            description=description,
        )
        event_id = event.id
        row = session.query(SecurityAlert).filter(
            SecurityAlert.anomaly_id == event_id
        ).first()
        alert_id = row.id if row is not None else None

    return {
        "scenario": scenario,
        "device_id": device_id,
        "messages_queued": queued.get("messages", 0),
        "simulator": queued or {"running": simulator.running, "note": sim_error},
        "anomaly_id": event_id,
        "alert_id": alert_id,
        "note": (
            "The scenario event/alert is labelled origin='scenario' and "
            "[SIMULATED]. Any additional anomaly produced by the model on the "
            "same traffic is a separate, independent detection."
        ),
    }
