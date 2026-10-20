"""Prometheus metrics for the P_310 backend.

Exposed at ``/metrics``.  Metric *values* are updated by real application
code paths - nothing here fabricates data.
"""

from __future__ import annotations

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)

APP_INFO = Info("p310_app", "P_310 application metadata")

# ---- Telemetry -------------------------------------------------------------
TELEMETRY_TOTAL = Counter(
    "p310_telemetry_messages_total",
    "Total telemetry messages ingested",
    ["source"],
)

TELEMETRY_PROCESSING_SECONDS = Histogram(
    "p310_telemetry_processing_seconds",
    "Time spent processing a single telemetry message",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

# ---- MQTT ------------------------------------------------------------------
MQTT_CONNECTED = Gauge(
    "p310_mqtt_connected",
    "1 when the backend is connected to a real MQTT broker, 0 otherwise",
)

MQTT_MESSAGES_RECEIVED = Counter(
    "p310_mqtt_messages_received_total",
    "MQTT messages received from the broker",
)

MQTT_RECONNECT_ATTEMPTS = Counter(
    "p310_mqtt_reconnect_attempts_total",
    "MQTT reconnection attempts",
)

MQTT_FALLBACK_ACTIVE = Gauge(
    "p310_mqtt_fallback_active",
    "1 when the in-process fallback transport is in use instead of MQTT",
)

# ---- Detection -------------------------------------------------------------
DETECTION_TOTAL = Counter(
    "p310_detections_total",
    "Anomaly detections produced by the model",
    ["result"],  # anomaly | normal
)

DETECTION_SECONDS = Histogram(
    "p310_detection_seconds",
    "Model inference duration",
    buckets=(0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
)

ANOMALIES_TOTAL = Counter(
    "p310_anomalies_total",
    "Anomaly events persisted",
    ["severity"],
)

MODEL_TRAINED = Gauge(
    "p310_model_trained",
    "1 when an active trained model artifact is available",
)

MODEL_TRAINING_RUNS = Counter(
    "p310_model_training_runs_total",
    "Number of model training runs completed",
    ["status"],
)

# ---- Alerts ----------------------------------------------------------------
ALERTS_TOTAL = Counter(
    "p310_alerts_total",
    "Security alerts created",
    ["severity"],
)

ALERTS_OPEN = Gauge(
    "p310_alerts_open",
    "Alerts whose status is not 'resolved'",
)

ALERTS_UNRESOLVED = Gauge(
    "p310_alerts_unresolved",
    "Alias gauge: alerts not yet resolved",
)

# ---- Simulation ------------------------------------------------------------
SIM_MESSAGES_TOTAL = Counter(
    "p310_simulation_messages_total",
    "Messages published by the device simulator",
)

SIM_SCENARIOS_TOTAL = Counter(
    "p310_simulation_scenarios_total",
    "Abnormal-behaviour scenarios injected",
    ["scenario"],
)

SIM_RUNNING = Gauge(
    "p310_simulation_running",
    "1 when the device simulator is running",
)

# ---- HTTP ------------------------------------------------------------------
HTTP_REQUESTS_TOTAL = Counter(
    "p310_http_requests_total",
    "Backend HTTP requests",
    ["method", "path", "status"],
)

HTTP_REQUEST_SECONDS = Histogram(
    "p310_http_request_seconds",
    "Backend HTTP request duration",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

ERRORS_TOTAL = Counter(
    "p310_errors_total",
    "Application errors by kind",
    ["kind"],
)

DEVICES_GAUGE = Gauge(
    "p310_devices",
    "Registered devices by status",
    ["status"],
)


def render_metrics() -> bytes:
    """Serialise the default registry for the /metrics endpoint."""
    return generate_latest()
