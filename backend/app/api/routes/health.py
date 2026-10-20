"""Application health and runtime status."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import __version__
from app.core.config import settings
from app.core.state import app_state
from app.db.database import get_db
from app.schemas.common import HealthOut

router = APIRouter(tags=["health"])


def _database_ok(session: Session) -> str:
    try:
        session.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:  # pragma: no cover - defensive
        return f"error: {exc.__class__.__name__}"


@router.get("/health", response_model=HealthOut, summary="Application health")
def health(session: Session = Depends(get_db)) -> HealthOut:
    mqtt = app_state.mqtt.snapshot()
    sim = app_state.simulation.snapshot()

    from app.services import model_info_service
    model_trained = model_info_service.is_model_available(session)

    return HealthOut(
        status="ok",
        app=settings.app_name,
        version=__version__,
        environment=settings.environment,
        uptime_seconds=round(app_state.uptime(), 2),
        database=_database_ok(session),
        mqtt_connected=mqtt["connected"],
        mqtt_mode=mqtt["mode"],
        mqtt_detail=mqtt["detail"],
        model_trained=model_trained,
        simulation_running=sim["running"],
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/health/live", summary="Liveness probe")
def live() -> dict:
    return {"status": "alive"}


@router.get("/health/ready", summary="Readiness probe")
def ready(session: Session = Depends(get_db)) -> dict:
    db = _database_ok(session)
    mqtt = app_state.mqtt.snapshot()
    return {
        "status": "ready" if db == "ok" else "degraded",
        "database": db,
        "mqtt_mode": mqtt["mode"],
    }
