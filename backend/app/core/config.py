"""Application configuration loaded from environment variables / .env file.

All settings are prefixed with ``P310_`` so they never collide with other
projects.  Secrets (if any) are only ever read from the environment; they are
never written to logs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    """Runtime settings for the P_310 backend."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        env_prefix="P310_",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Application ----
    app_name: str = "P310 IoT Anomaly Detection"
    environment: str = "development"
    debug: bool = True
    host: str = "127.0.0.1"
    port: int = 8000

    # ---- Database ----
    database_url: str = "sqlite:///./p310.db"

    # ---- CORS ----
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- MQTT ----
    mqtt_enabled: bool = True
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_keepalive: int = 30
    mqtt_client_id: str = "p310_backend"
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_connect_timeout: float = 4.0

    mqtt_topic_telemetry: str = "iot/devices"
    mqtt_topic_status_suffix: str = "status"
    mqtt_topic_alerts: str = "iot/security/alerts"

    # ---- Simulation ----
    sim_default_device_count: int = 6
    sim_tick_seconds: float = 2.0
    sim_anomaly_probability: float = 0.06
    # Hard cap on simulated devices (guards against absurd API input).
    sim_max_devices: int = 32
    # Start the simulator automatically when the API process boots.
    sim_auto_start: bool = False
    # Messages emitted per injected scenario (unless the request overrides it).
    sim_inject_messages: int = 12

    # ---- Machine learning ----
    ml_model_path: str = "./app/ml/artifacts/isolation_forest.joblib"
    ml_model_config_path: str = "./app/ml/artifacts/model_config.json"
    ml_contamination: float = 0.05
    # Fraction of the *training* distribution expected to be flagged at
    # inference time.  The decision threshold and the suspicious/critical
    # severity bands are calibrated from this quantile, which keeps alert
    # volume predictable independently of the estimator's contamination.
    # Set ml_anomaly_threshold to a non-zero value to override it manually.
    ml_flag_quantile: float = 0.05
    # Threshold on the IsolationForest decision score (lower = more
    # sensitive).  0.0 means "auto": calibrate from ml_flag_quantile.
    ml_anomaly_threshold: float = 0.0
    ml_random_seed: int = 42

    # ---- Features ----
    # Sliding look-back window (seconds) used by stream feature extraction.
    # Must match the value used during training to avoid train/serve skew.
    feature_window_size: int = 30
    # Minimum seconds between two persisted anomaly events for the same
    # device.  Prevents an attack simulation from flooding the database.
    detection_cooldown_s: float = 10.0
    # Per-device rolling history length (messages) retained in memory.
    detection_history_len: int = 500

    # ---- Logging ----
    log_level: str = "INFO"
    log_file: str = "./logs/backend.log"

    # ---- Derived helpers -----------------------------------------------
    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def model_path_absolute(self) -> Path:
        p = Path(self.ml_model_path)
        return p if p.is_absolute() else (BACKEND_DIR / p).resolve()

    @property
    def model_config_path_absolute(self) -> Path:
        p = Path(self.ml_model_config_path)
        return p if p.is_absolute() else (BACKEND_DIR / p).resolve()

    @property
    def database_path_absolute(self) -> Path:
        """Filesystem path of the SQLite database (empty for other engines)."""
        url = self.database_url
        if url.startswith("sqlite:///"):
            raw = url[len("sqlite:///"):]
            p = Path(raw)
            return p if p.is_absolute() else (BACKEND_DIR / p).resolve()
        return Path(".")

    @field_validator("ml_contamination")
    @classmethod
    def _check_contamination(cls, v: float) -> float:
        if not 0.0 < v < 0.5:
            raise ValueError("ml_contamination must be between 0 and 0.5")
        return v

    @field_validator("ml_flag_quantile")
    @classmethod
    def _check_flag_quantile(cls, v: float) -> float:
        if not 0.0 < v < 0.5:
            raise ValueError("ml_flag_quantile must be between 0 and 0.5")
        return v

    @field_validator("sim_anomaly_probability")
    @classmethod
    def _check_prob(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("sim_anomaly_probability must be within [0, 1]")
        return v


@lru_cache
def get_settings() -> Settings:
    """Return a cached settings instance."""
    return Settings()


settings = get_settings()
