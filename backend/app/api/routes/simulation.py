"""Simulation and MQTT control endpoints."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.core.logging_config import get_logger
from app.db.database import session_scope
from app.db.models import SimulationSession
from app.mqtt.manager import mqtt_manager
from app.runtime import inject_scenario
from app.simulator.device_simulator import SCENARIOS, simulator

router = APIRouter(prefix="/simulation", tags=["simulation"])
log = get_logger("api.simulation")


class SimulationStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_count: Optional[int] = Field(None, ge=1, le=64)
    seed: Optional[int] = Field(None, ge=0, le=2**31 - 1)


class ScenarioInjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., min_length=3, max_length=64)
    scenario: str = Field(..., max_length=48)
    messages: Optional[int] = Field(None, ge=1, le=500)
    description: str = Field("", max_length=500)


@router.get("/status", summary="Simulator and MQTT status")
def status() -> Dict[str, Any]:
    return {
        "simulation": simulator.status(),
        "mqtt": mqtt_manager.status(),
        "scenarios": list(SCENARIOS),
    }


@router.post("/start", summary="Start the device simulator")
def start(payload: Optional[SimulationStartRequest] = None) -> Dict[str, Any]:
    payload = payload or SimulationStartRequest()
    if simulator.running:
        raise HTTPException(status_code=409, detail="Simulation already running")
    if mqtt_manager.mode == "disabled":
        raise HTTPException(
            status_code=409,
            detail="MQTT is disabled (P310_MQTT_ENABLED=false); there is no "
                   "transport to publish on.",
        )
    result = simulator.start(device_count=payload.device_count, seed=payload.seed)
    if not result.get("running"):
        raise HTTPException(status_code=500, detail="Simulator failed to start")
    return result


@router.post("/stop", summary="Stop the device simulator")
def stop() -> Dict[str, Any]:
    if not simulator.running:
        raise HTTPException(status_code=409, detail="Simulation is not running")
    return simulator.stop()


@router.post("/inject", summary="Inject an attack scenario")
def inject(payload: ScenarioInjectRequest) -> Dict[str, Any]:
    try:
        return inject_scenario(
            device_id=payload.device_id,
            scenario=payload.scenario,
            messages=payload.messages,
            description=payload.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0] if exc.args else exc))


@router.get("/devices", summary="Devices in the running simulation")
def devices() -> Dict[str, Any]:
    state = simulator.status()
    return {"running": state.get("running", False), "devices": state.get("devices", [])}


@router.get("/sessions", summary="Recent simulation sessions")
def sessions(limit: int = Query(20, ge=1, le=100)) -> Dict[str, Any]:
    with session_scope() as session:
        rows = (
            session.query(SimulationSession)
            .order_by(SimulationSession.started_at.desc())
            .limit(limit)
            .all()
        )
        return {
            "items": [
                {
                    "id": r.id,
                    "status": r.status,
                    "scenario": r.scenario,
                    "device_count": r.device_count,
                    "messages_published": r.messages_published,
                    "anomalies_generated": r.anomalies_generated,
                    "transport": r.transport,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "stopped_at": r.stopped_at.isoformat() if r.stopped_at else None,
                    "notes": r.notes,
                }
                for r in rows
            ]
        }


# ---------------------------------------------------------------------------
# MQTT control
# ---------------------------------------------------------------------------
mqtt_router = APIRouter(prefix="/mqtt", tags=["mqtt"])


@mqtt_router.get("/status", summary="MQTT transport status")
def mqtt_status() -> Dict[str, Any]:
    return mqtt_manager.status()


@mqtt_router.post("/reconnect", summary="Re-establish the MQTT transport")
def mqtt_reconnect(
    force_fallback: bool = Body(False, embed=True),
) -> Dict[str, Any]:
    mode = mqtt_manager.start(force_fallback=force_fallback)
    return {"mode": mode, "mqtt": mqtt_manager.status()}


@mqtt_router.post("/disconnect", summary="Stop the MQTT transport")
def mqtt_disconnect() -> Dict[str, Any]:
    mqtt_manager.stop()
    return mqtt_manager.status()
