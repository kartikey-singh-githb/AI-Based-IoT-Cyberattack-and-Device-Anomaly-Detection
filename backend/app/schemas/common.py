"""Pydantic request/response schemas for the P_310 REST API.

Every schema validates input explicitly; unknown fields are rejected so that
malformed payloads cannot silently reach the database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Generic
# ---------------------------------------------------------------------------
class ErrorBody(BaseModel):
    detail: str


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------
DeviceStatus = Literal["online", "offline", "degraded", "maintenance"]

class DeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., min_length=3, max_length=64,
                           pattern=r"^[A-Za-z0-9_\-\.]+$")
    name: str = Field(..., min_length=1, max_length=128)
    device_type: str = Field(..., min_length=2, max_length=64)
    status: DeviceStatus = "offline"
    location: str = Field("", max_length=128)
    firmware: str = Field("1.0.0", max_length=32)
    is_simulated: bool = True
    behaviour_label: str = Field("normal", max_length=48)
    expected_interval_s: float = Field(2.0, gt=0, le=3600)
    meta: Dict[str, Any] = Field(default_factory=dict)


class DeviceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=128)
    status: Optional[DeviceStatus] = None
    location: Optional[str] = Field(None, max_length=128)
    behaviour_label: Optional[str] = Field(None, max_length=48)
    expected_interval_s: Optional[float] = Field(None, gt=0, le=3600)


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: str
    name: str
    device_type: str
    status: str
    location: str
    firmware: str
    is_simulated: bool
    behaviour_label: str
    message_count: int
    anomaly_count: int
    expected_interval_s: float
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    meta: Dict[str, Any] = Field(default_factory=dict)
    latest_reading: Optional[Dict[str, Any]] = None


class DeviceList(BaseModel):
    items: List[DeviceOut]
    meta: PageMeta


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------
class TelemetryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., min_length=3, max_length=64)
    timestamp: Optional[datetime] = None
    status: str = Field("online", max_length=24)
    readings: Dict[str, Any] = Field(default_factory=dict)
    is_synthetic: bool = True
    ingest_source: str = Field("api", max_length=24)

    @field_validator("readings")
    @classmethod
    def _readings_not_empty(cls, v):
        if not isinstance(v, dict):
            raise ValueError("readings must be an object")
        return v


class TelemetryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    timestamp: datetime
    topic: str
    device_status: str
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    energy_w: Optional[float] = None
    voltage: Optional[float] = None
    current_a: Optional[float] = None
    rssi: Optional[float] = None
    readings: Dict[str, Any] = Field(default_factory=dict)
    is_synthetic: bool
    ingest_source: str


class TelemetryList(BaseModel):
    items: List[TelemetryOut]
    meta: PageMeta


# ---------------------------------------------------------------------------
# Anomalies
# ---------------------------------------------------------------------------
class AnomalyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    timestamp: datetime
    score: Optional[float] = None
    is_anomaly: bool
    severity: str
    threshold: float
    model_version: str
    description: str
    status: str
    origin: str = "detector"
    is_synthetic: bool
    features: Dict[str, Any] = Field(default_factory=dict)
    deviations: Dict[str, Any] = Field(default_factory=dict)
    alert_id: Optional[int] = None


class AnomalyList(BaseModel):
    items: List[AnomalyOut]
    meta: PageMeta


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
class AlertCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., min_length=3, max_length=64)
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    category: str = Field("manual", max_length=64)
    title: str = Field(..., min_length=3, max_length=255)
    description: str = Field("", max_length=4000)


class AlertUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Optional[Literal["open", "acknowledged", "resolved"]] = None
    assignee: Optional[str] = Field(None, max_length=128)
    resolution_note: Optional[str] = Field(None, max_length=4000)


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    anomaly_id: Optional[int] = None
    device_id: str
    timestamp: datetime
    severity: str
    category: str
    title: str
    description: str
    status: str
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    resolution_note: str
    assignee: str
    origin: str
    is_synthetic: bool


class AlertList(BaseModel):
    items: List[AlertOut]
    meta: PageMeta


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
class TimeseriesPoint(BaseModel):
    bucket: str
    value: float


class OverviewStats(BaseModel):
    total_devices: int
    online_devices: int
    offline_devices: int
    degraded_devices: int
    total_telemetry: int
    telemetry_last_hour: int
    total_anomalies: int
    anomalies_last_hour: int
    open_alerts: int
    critical_alerts: int
    unresolved_alerts: int
    mqtt_connected: bool
    mqtt_mode: str
    model_trained: bool
    simulation_running: bool
    generated_at: datetime


class AnalyticsSummary(BaseModel):
    telemetry_by_hour: List[TimeseriesPoint]
    anomalies_by_hour: List[TimeseriesPoint]
    alerts_by_severity: List[TimeseriesPoint]
    device_health: List[TimeseriesPoint]
    top_anomaly_devices: List[Dict[str, Any]]
    telemetry_by_device: List[Dict[str, Any]]
    generated_at: datetime


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
class ModelInfo(BaseModel):
    trained: bool
    algorithm: str
    version: Optional[str] = None
    trained_at: Optional[datetime] = None
    training_rows: int = 0
    features: List[str] = []
    feature_count: int = 0
    feature_space: str = "stream"
    contamination: float = 0.0
    threshold: float = 0.0
    flag_quantile: Optional[float] = None
    # Severity bands calibrated from the training-score distribution:
    # score <= critical_level -> critical, score <= threshold -> suspicious.
    critical_level: Optional[float] = None
    suspicious_level: Optional[float] = None
    n_estimators: Optional[int] = None
    training_seconds: Optional[float] = None
    random_seed: int = 0
    dataset_name: str = ""
    dataset_kind: str = ""
    dataset_source: str = ""
    metrics: Dict[str, Any] = {}
    metrics_are_measured: bool = False
    notes: str = ""
    artifact_path: str = ""
    artifact_exists: bool = False
    baseline: Dict[str, Any] = {}
    caveat: str = ""


class TrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: Literal["synthetic", "csv", "parquet", "file"] = "synthetic"
    csv_path: Optional[str] = Field(
        None, max_length=1024,
        description=(
            "Backwards-compatible path to a CSV or Parquet file, relative to "
            "data/datasets or absolute."
        ),
    )
    dataset_path: Optional[str] = Field(
        None, max_length=1024,
        description=(
            "Path to a CSV or Parquet dataset file. Takes precedence over "
            "csv_path. The path is resolved on the backend host."
        ),
    )
    contamination: Optional[float] = Field(None, gt=0, lt=0.5)
    threshold: Optional[float] = None
    offline_fit_on_benign: Optional[bool] = Field(
        None,
        description=(
            "Offline dataset models: fit on benign training rows when labels "
            "are available. Defaults to the backend setting."
        ),
    )
    calibration_min_precision: Optional[float] = Field(
        None, ge=0, le=1,
        description=(
            "Offline dataset models: validation precision floor when choosing "
            "the F1-maximising alert threshold. Use 0 for pure F1."
        ),
    )
    calibration_max_fpr: Optional[float] = Field(
        None, ge=0, le=1,
        description=(
            "Offline dataset models: optional validation false-positive-rate "
            "cap for threshold selection."
        ),
    )
    label_column: Optional[str] = Field(
        None, max_length=64,
        json_schema_extra={
            "help": "Optional ground-truth label column for evaluation metrics."
        },
    )
    limit_rows: Optional[int] = Field(
        None, ge=0,
        description=(
            "Reproducible random sample size. Omit for the default 50,000; "
            "use 0 to load every row."
        ),
    )

    @field_validator("csv_path", "dataset_path", mode="before")
    @classmethod
    def _blank_path_to_none(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class TrainResponse(BaseModel):
    status: str
    version: str
    training_rows: int
    feature_count: int
    features: List[str]
    contamination: float
    threshold: float
    dataset_name: str
    dataset_kind: str
    metrics: Dict[str, Any]
    metrics_are_measured: bool
    message: str
    feature_space: str = "stream"
    offline: bool = False
    artifact_path: Optional[str] = None
    artifact_config_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------
class DatasetInfo(BaseModel):
    id: str
    name: str
    kind: Literal["synthetic", "public"]
    description: str
    source: str
    download_page: str
    license: str
    format: str
    features: str
    preprocessing: str
    available: bool
    local_path: Optional[str] = None
    rows: Optional[int] = None
    size_bytes: Optional[int] = None
    notes: str = ""


class DatasetList(BaseModel):
    items: List[DatasetInfo]


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
class SimulationStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_count: int = Field(6, ge=1, le=50)
    tick_seconds: float = Field(2.0, gt=0.1, le=60)
    anomaly_probability: Optional[float] = Field(None, ge=0, le=1)
    device_types: Optional[List[str]] = None


class SimulationStatus(BaseModel):
    running: bool
    session_id: Optional[int] = None
    device_count: int = 0
    tick_seconds: float = 0.0
    anomaly_probability: float = 0.0
    messages_published: int = 0
    anomalies_injected: int = 0
    started_at: Optional[datetime] = None
    transport: str = "unspecified"
    mqtt_connected: bool = False


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: Literal[
        "message_flood",
        "sensor_spike",
        "connection_storm",
        "telemetry_blackout",
        "unauthorized_access",
        "credential_bruteforce",
    ]
    device_id: Optional[str] = Field(None, max_length=64)
    duration_seconds: float = Field(10.0, gt=0, le=300)


class ScenarioResponse(BaseModel):
    status: str
    scenario: str
    device_id: str
    messages_injected: int
    expected_effects: str
    session_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Health / status
# ---------------------------------------------------------------------------
class HealthOut(BaseModel):
    status: str
    app: str
    version: str
    environment: str
    uptime_seconds: float
    database: str
    mqtt_connected: bool
    mqtt_mode: str
    mqtt_detail: str
    model_trained: bool
    simulation_running: bool
    timestamp: datetime
