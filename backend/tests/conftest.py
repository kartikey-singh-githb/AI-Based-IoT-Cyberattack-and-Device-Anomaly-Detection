"""Shared pytest fixtures.

Environment variables are pinned **before** any ``app`` import so that
``settings`` (which is cached at import time) never touches the developer's
real database, model artifact or log file.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(__file__).resolve().parent / "_tmp"
TMP_DIR.mkdir(exist_ok=True)

os.environ.update(
    {
        "P310_ENVIRONMENT": "test",
        "P310_DATABASE_URL": f"sqlite:///{(TMP_DIR / 'test.db').as_posix()}",
        "P310_ML_MODEL_PATH": str(TMP_DIR / "model.joblib"),
        "P310_ML_MODEL_CONFIG_PATH": str(TMP_DIR / "model_config.json"),
        "P310_ML_OFFLINE_MODEL_DIR": str(TMP_DIR / "offline"),
        "P310_LOG_FILE": str(TMP_DIR / "test.log"),
        "P310_LOG_LEVEL": "WARNING",
        "P310_SIM_AUTO_START": "false",
        "P310_MQTT_ENABLED": "true",
        "P310_MQTT_CONNECT_TIMEOUT": "0.3",
        "P310_ML_CONTAMINATION": "0.05",
        "P310_ML_FLAG_QUANTILE": "0.05",
        "P310_FEATURE_WINDOW_SIZE": "30",
        "P310_DETECTION_COOLDOWN_S": "0.0",
    }
)


@pytest.fixture(scope="session")
def app():
    from app.main import app as fastapi_app

    return fastapi_app


@pytest.fixture(scope="session")
def client(app):
    """Lifespan-managed TestClient shared by the whole session."""
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_state(client):
    """Empty the database and reset in-process caches before every test."""
    from app.core.config import settings
    from app.core.state import app_state
    from app.db.database import session_scope
    from app.db.models import Base
    from app.services import detection_service, model_info_service
    from app.simulator.device_simulator import simulator

    if simulator.running:
        simulator.stop()
    detection_service.reset_runtime()
    model_info_service.reset_cache()

    # Remove any artifact left behind by a previous test so "untrained"
    # assertions hold and stale models cannot leak between tests.
    for path in (settings.model_path_absolute, settings.model_config_path_absolute):
        try:
            path.unlink()
        except OSError:
            pass
    shutil.rmtree(settings.offline_model_dir_absolute, ignore_errors=True)

    with session_scope() as session:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())

    app_state.latest_telemetry.clear()
    app_state.simulation.messages_published = 0
    app_state.simulation.anomalies_injected = 0
    app_state.simulation.running = False

    yield

    if simulator.running:
        simulator.stop()
    model_info_service.reset_cache()
    detection_service.reset_runtime()


@pytest.fixture
def session_factory():
    from app.db.database import session_scope

    return session_scope


def ingest(client, device_id: str, readings: dict, *, status: str = "online",
           n: int = 1, **extra):
    """POST ``n`` telemetry records; returns the last response."""
    last = None
    for _ in range(n):
        last = client.post(
            "/telemetry",
            json={
                "device_id": device_id,
                "status": status,
                "readings": readings,
                **extra,
            },
        )
    return last
