"""FastAPI application entry point.

Run with::

    uvicorn app.main:app --host 127.0.0.1 --port 8000

Lifecycle
---------
startup  -> initialise logging, open the SQLite schema, load a trained model
            artifact if one exists, and (in stage 3+) start the MQTT client
            and device simulator.
shutdown -> stop background services and dispose of the DB engine.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import (
    alerts,
    analytics,
    anomalies,
    devices,
    health,
    model,
    simulation,
    telemetry,
)
from app.core.config import settings
from app.core.logging_config import get_logger, setup_logging
from app.core.metrics import (
    APP_INFO,
    ERRORS_TOTAL,
    HTTP_REQUEST_SECONDS,
    HTTP_REQUESTS_TOTAL,
)

log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    log.info("Starting %s v%s (%s)", settings.app_name, __version__,
             settings.environment)

    from app.db.database import init_db

    init_db()

    from app.services import model_info_service

    model_info_service.get_detector()          # load artifact if present
    from app.db.database import session_scope
    from app.services import device_service

    with session_scope() as session:
        device_service.refresh_device_gauge(session)

    # Background services: MQTT (real broker or the labelled in-process
    # fallback) and, when configured, the device simulator.
    from app import runtime

    runtime.start_background_services()

    try:
        yield
    finally:
        runtime.stop_background_services()
        log.info("Shutting down")
        from app.db.database import reset_engine

        reset_engine()


def _build_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=(
            "AI-based IoT cyberattack and device anomaly detection.\n\n"
            "**All telemetry in this application is simulated.**  The "
            "Isolation Forest detector reports statistically unusual "
            "behaviour; a detection is not a confirmed attack."
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        max_age=600,
    )

    # ---- metrics middleware -------------------------------------------
    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        # Collapse high-cardinality ids so Prometheus stays bounded.
        route = request.scope.get("route")
        path = getattr(route, "path", None) or _normalise_path(request.url.path)
        HTTP_REQUESTS_TOTAL.labels(
            method=request.method, path=path, status=str(response.status_code)
        ).inc()
        HTTP_REQUEST_SECONDS.labels(method=request.method, path=path).observe(elapsed)
        response.headers["X-Process-Time-Ms"] = f"{elapsed * 1000:.2f}"
        return response

    # ---- error handlers ------------------------------------------------
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        ERRORS_TOTAL.labels(kind="unhandled").inc()
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: Exception):
        return JSONResponse(status_code=404, content={"detail": "Not found"})

    # ---- routes --------------------------------------------------------
    app.include_router(health.router)
    app.include_router(devices.router)
    app.include_router(telemetry.router)
    app.include_router(anomalies.router)
    app.include_router(alerts.router)
    app.include_router(analytics.router)
    app.include_router(model.router)
    app.include_router(simulation.router)
    app.include_router(simulation.mqtt_router)

    # ---- Prometheus ----------------------------------------------------
    from app.core.metrics import render_metrics

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(content=render_metrics(),
                        media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {
            "name": settings.app_name,
            "version": __version__,
            "docs": "/docs",
            "health": "/health",
            "metrics": "/metrics",
        }

    APP_INFO.info({
        "version": __version__,
        "environment": settings.environment,
        "python": __import__("platform").python_version(),
    })
    return app


def _normalise_path(path: str) -> str:
    """Replace path parameters to keep metric cardinality low."""
    parts = []
    for segment in path.strip("/").split("/"):
        if not segment:
            continue
        parts.append("{param}" if segment and segment[0].isdigit()
                     or (len(segment) > 8 and "-" in segment)
                     else segment)
    return "/" + "/".join(parts) if parts else "/"


app = _build_app()
