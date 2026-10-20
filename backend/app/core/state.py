"""Mutable in-process runtime state (connection flags, runtimes).

Kept separate from the database because it describes *live* conditions that
must never be persisted: MQTT link state, simulator loop handle, etc.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class MqttState:
    enabled: bool = True
    connected: bool = False
    mode: str = "disconnected"   # connected | fallback | disabled | connecting
    detail: str = "MQTT not started"
    last_error: str = ""
    messages_received: int = 0
    last_message_at: Optional[float] = None
    broker: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "enabled": self.enabled,
                "connected": self.connected,
                "mode": self.mode,
                "detail": self.detail,
                "messages_received": self.messages_received,
                "last_message_at": self.last_message_at,
                "broker": self.broker,
            }


@dataclass
class SimulationState:
    running: bool = False
    session_id: Optional[int] = None
    device_count: int = 0
    tick_seconds: float = 2.0
    anomaly_probability: float = 0.06
    messages_published: int = 0
    anomalies_injected: int = 0
    started_at: Optional[float] = None
    transport: str = "unspecified"
    lock: threading.RLock = field(default_factory=threading.RLock)

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "running": self.running,
                "session_id": self.session_id,
                "device_count": self.device_count,
                "tick_seconds": self.tick_seconds,
                "anomaly_probability": self.anomaly_probability,
                "messages_published": self.messages_published,
                "anomalies_injected": self.anomalies_injected,
                "started_at": self.started_at,
                "transport": self.transport,
            }


class AppState:
    """Container for all live runtime state."""

    def __init__(self) -> None:
        self.started_at = time.time()
        self.mqtt = MqttState()
        self.simulation = SimulationState()
        # device_id -> latest telemetry row id (fast overview lookups)
        self.latest_telemetry: Dict[str, int] = {}
        self._lock = threading.RLock()

    # -- lifecycle helpers -------------------------------------------------
    def uptime(self) -> float:
        return time.time() - self.started_at

    def note_latest_telemetry(self, device_id: str, row_id: int) -> None:
        with self._lock:
            self.latest_telemetry[device_id] = row_id

    def latest_telemetry_id(self, device_id: str) -> Optional[int]:
        with self._lock:
            return self.latest_telemetry.get(device_id)


app_state = AppState()
