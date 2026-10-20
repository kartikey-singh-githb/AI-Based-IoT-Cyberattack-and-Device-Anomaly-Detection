"""SQLAlchemy ORM models for P_310.

Tables
------
devices            - registered (simulated) IoT devices
telemetry          - ingested telemetry records
anomaly_events     - Isolation Forest detection results
security_alerts    - alerts raised from anomaly events
simulation_sessions- start/stop record of simulation runs
model_metadata     - trained model provenance & configuration
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def utcnow() -> datetime:
    """Timezone-aware UTC timestamp (naive - SQLite stores no tz)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def json_dumps(value) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


class Device(Base):
    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    device_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="offline", index=True)
    location: Mapped[str] = mapped_column(String(128), default="")
    firmware: Mapped[str] = mapped_column(String(32), default="1.0.0")

    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    # Label attached by the simulator: "normal" or a named attack scenario.
    behaviour_label: Mapped[str] = mapped_column(String(48), default="normal")

    message_count: Mapped[int] = mapped_column(Integer, default=0)
    anomaly_count: Mapped[int] = mapped_column(Integer, default=0)
    expected_interval_s: Mapped[float] = mapped_column(Float, default=2.0)

    first_seen: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow,
                                                       onupdate=utcnow)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")

    telemetry = relationship("Telemetry", back_populates="device",
                             cascade="all, delete-orphan",
                             passive_deletes=True,
                             order_by="Telemetry.timestamp.desc()")

    @property
    def meta(self) -> dict:
        try:
            return json.loads(self.meta_json or "{}")
        except json.JSONDecodeError:
            return {}


class Telemetry(Base):
    __tablename__ = "telemetry"
    __table_args__ = (
        Index("ix_telemetry_device_ts", "device_id", "timestamp"),
        Index("ix_telemetry_ts", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.device_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow,
                                                nullable=False)
    topic: Mapped[str] = mapped_column(String(255), default="")
    device_status: Mapped[str] = mapped_column(String(24), default="online")

    # Frequently queried readings kept as columns for SQL analytics.
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity: Mapped[float | None] = mapped_column(Float, nullable=True)
    energy_w: Mapped[float | None] = mapped_column(Float, nullable=True)
    voltage: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    rssi: Mapped[float | None] = mapped_column(Float, nullable=True)

    readings_json: Mapped[str] = mapped_column(Text, default="{}")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    ingest_source: Mapped[str] = mapped_column(String(24), default="simulation",
                                               index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    device = relationship("Device", back_populates="telemetry")

    @property
    def readings(self) -> dict:
        try:
            return json.loads(self.readings_json or "{}")
        except json.JSONDecodeError:
            return {}


class AnomalyEvent(Base):
    __tablename__ = "anomaly_events"
    __table_args__ = (
        Index("ix_anomaly_device_ts", "device_id", "timestamp"),
        Index("ix_anomaly_ts", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow,
                                                nullable=False)

    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_anomaly: Mapped[bool] = mapped_column(Boolean, default=False)
    # normal | suspicious | critical  (classification of the *detection*)
    severity: Mapped[str] = mapped_column(String(24), default="normal", index=True)
    threshold: Mapped[float] = mapped_column(Float, default=0.0)
    model_version: Mapped[str] = mapped_column(String(48), default="untrained")

    features_json: Mapped[str] = mapped_column(Text, default="{}")
    deviations_json: Mapped[str] = mapped_column(Text, default="{}")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="new", index=True)
    # "detector" = emitted by the Isolation Forest pipeline; "scenario" =
    # injected by the simulator (always also flagged is_synthetic).
    origin: Mapped[str] = mapped_column(String(32), default="detector",
                                        index=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    alert = relationship("SecurityAlert", back_populates="anomaly",
                         uselist=False, cascade="all, delete-orphan")

    @property
    def features(self) -> dict:
        try:
            return json.loads(self.features_json or "{}")
        except json.JSONDecodeError:
            return {}

    @property
    def deviations(self) -> dict:
        try:
            return json.loads(self.deviations_json or "{}")
        except json.JSONDecodeError:
            return {}


class SecurityAlert(Base):
    __tablename__ = "security_alerts"
    __table_args__ = (
        Index("ix_alert_device_ts", "device_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anomaly_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("anomaly_events.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow,
                                                nullable=False)

    severity: Mapped[str] = mapped_column(String(24), default="medium", index=True)
    category: Mapped[str] = mapped_column(String(64), default="anomaly", index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    assignee: Mapped[str] = mapped_column(String(128), default="")

    # "detection" = produced by the ML pipeline, "scenario" = simulator-injected
    origin: Mapped[str] = mapped_column(String(24), default="detection")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    anomaly = relationship("AnomalyEvent", back_populates="alert")


class SimulationSession(Base):
    __tablename__ = "simulation_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    scenario: Mapped[str] = mapped_column(String(64), default="normal")
    device_count: Mapped[int] = mapped_column(Integer, default=0)
    messages_published: Mapped[int] = mapped_column(Integer, default=0)
    anomalies_generated: Mapped[int] = mapped_column(Integer, default=0)
    transport: Mapped[str] = mapped_column(String(24), default="unspecified")
    notes: Mapped[str] = mapped_column(Text, default="")


class ModelMetadata(Base):
    __tablename__ = "model_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    algorithm: Mapped[str] = mapped_column(String(64), default="IsolationForest")
    trained_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    training_rows: Mapped[int] = mapped_column(Integer, default=0)
    feature_names_json: Mapped[str] = mapped_column(Text, default="[]")
    # "stream"  -> the online feature space used by the live detector
    # "dataset" -> arbitrary numeric columns from a public dataset CSV;
    #              such a model can only score that dataset's own rows.
    feature_space: Mapped[str] = mapped_column(String(24), default="stream")
    contamination: Mapped[float] = mapped_column(Float, default=0.05)
    threshold: Mapped[float] = mapped_column(Float, default=-0.5)
    random_seed: Mapped[int] = mapped_column(Integer, default=42)

    # Provenance
    dataset_name: Mapped[str] = mapped_column(String(128), default="synthetic")
    dataset_kind: Mapped[str] = mapped_column(String(32), default="synthetic")
    dataset_source: Mapped[str] = mapped_column(String(255), default="")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    notes: Mapped[str] = mapped_column(Text, default="")

    @property
    def feature_names(self) -> list:
        try:
            return json.loads(self.feature_names_json or "[]")
        except json.JSONDecodeError:
            return []

    @property
    def metrics(self) -> dict:
        try:
            return json.loads(self.metrics_json or "{}")
        except json.JSONDecodeError:
            return {}
