"""Virtual IoT device simulator.

Devices are created from :mod:`app.data.device_profiles` and publish at their
profile interval (with jitter) through the MQTT transport.  When no broker is
available the transport is the labelled in-process fallback, so the publish ->
subscribe -> ingest path is still exercised end to end.

Two sources of abnormal behaviour:

* **Injections** - the API schedules a named scenario on a specific device
  (see :func:`inject_scenario`).  The simulator emits the anomalous telemetry
  *and* records a clearly-labelled scenario event/alert.
* **Ambient anomalies** - a small per-tick probability produces unprompted
  abnormal traffic, so the dashboard has something to detect without a demo
  operator clicking anything.

Everything this module produces is simulated data; rows are marked
``is_synthetic=True`` and devices ``is_simulated=True``.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.metrics import (
    SIM_MESSAGES_TOTAL,
    SIM_RUNNING,
    SIM_SCENARIOS_TOTAL,
)
from app.core.state import app_state
from app.data.device_profiles import DEFAULT_DEVICE_TYPES, DEVICE_PROFILES
from app.db.database import session_scope
from app.db.models import SimulationSession
from app.mqtt import publisher as mqtt_publisher
from app.schemas.common import TelemetryIn
from app.services import device_service, telemetry_service

log = get_logger("simulator")

# How much faster than the profile interval a message flood publishes.
FLOOD_FACTOR = 0.12
# A blackout swallows this many intervals of silence.
BLACKOUT_INTERVALS = (6, 12)

SCENARIOS = (
    "message_flood",
    "sensor_spike",
    "telemetry_blackout",
    "connection_storm",
    "unauthorized_access",
)


@dataclass
class _DeviceState:
    device_id: str
    device_type: str
    interval_s: float
    rng: random.Random
    next_due: float = 0.0
    burst_remaining: int = 0
    blackout_remaining: int = 0
    pending_scenario: Optional[str] = None
    readings: Dict[str, float] = field(default_factory=dict)
    status: str = "online"
    connection_attempts: int = 0


class DeviceSimulator:
    """Threaded publisher for a fleet of virtual devices."""

    def __init__(self) -> None:
        self._devices: List[_DeviceState] = []
        self._thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()
        self._lock = threading.RLock()
        self._rng = random.Random(settings.ml_random_seed)
        self._session_id: Optional[int] = None
        self.messages_published = 0
        self.scenarios_generated = 0

    # -- lifecycle ---------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, *, device_count: Optional[int] = None,
              seed: Optional[int] = None) -> Dict[str, Any]:
        with self._lock:
            if self.running:
                return self.status()
            count = device_count or settings.sim_default_device_count
            count = max(1, min(count, settings.sim_max_devices))
            self._rng = random.Random(seed if seed is not None
                                      else settings.ml_random_seed)
            self._stopping.clear()
            self.messages_published = 0
            self.scenarios_generated = 0
            self._devices = self._register_devices(count)
            self._session_id = self._open_session(count)

        self._thread = threading.Thread(
            target=self._run, name="p310-simulator", daemon=True
        )
        self._thread.start()
        SIM_RUNNING.set(1)
        app_state.simulation.running = True
        app_state.simulation.device_count = count
        app_state.simulation.tick_seconds = settings.sim_tick_seconds
        app_state.simulation.anomaly_probability = settings.sim_anomaly_probability
        app_state.simulation.started_at = time.time()
        app_state.simulation.session_id = self._session_id
        app_state.simulation.transport = app_state.mqtt.mode
        log.info("Simulator started: %d devices, transport=%s",
                 count, app_state.mqtt.mode)
        return self.status()

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            thread, self._thread = self._thread, None
            self._stopping.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        SIM_RUNNING.set(0)
        app_state.simulation.running = False
        self._close_session()
        log.info("Simulator stopped after %d messages", self.messages_published)
        return self.status()

    # -- scenario injection -------------------------------------------------
    def inject(self, device_id: str, scenario: str,
               messages: Optional[int] = None) -> Dict[str, Any]:
        """Schedule abnormal traffic for one device; returns what was queued."""
        if scenario not in SCENARIOS:
            raise ValueError(
                f"Unknown scenario '{scenario}'. Valid: {', '.join(SCENARIOS)}"
            )
        with self._lock:
            state = next((d for d in self._devices if d.device_id == device_id),
                         None)
            if state is None:
                # The device may not be part of the running fleet: fall back
                # to whatever device is registered, or raise clearly.
                raise KeyError(
                    f"Device '{device_id}' is not in the running simulation"
                )
            count = messages if messages and messages > 0 else _default_messages(scenario)
            now = time.time()
            if scenario == "message_flood":
                state.burst_remaining = max(count, 8)
                state.next_due = min(state.next_due, now)
            elif scenario == "telemetry_blackout":
                state.blackout_remaining = max(count // 2, BLACKOUT_INTERVALS[0])
                state.next_due = now + state.interval_s
            else:
                state.pending_scenario = scenario
                state.next_due = min(state.next_due, now)

        self.scenarios_generated += 1
        SIM_SCENARIOS_TOTAL.labels(scenario=scenario).inc()
        app_state.simulation.anomalies_injected += 1
        log.info("Scheduled scenario %s on %s (%s messages)",
                 scenario, device_id, count)
        return {
            "device_id": device_id,
            "scenario": scenario,
            "messages": count,
            "transport": app_state.mqtt.mode,
        }

    # -- status --------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        with self._lock:
            devices = [
                {
                    "device_id": d.device_id,
                    "device_type": d.device_type,
                    "next_due_in": round(max(0.0, d.next_due - time.time()), 2),
                    "burst_remaining": d.burst_remaining,
                    "blackout_remaining": d.blackout_remaining,
                    "pending_scenario": d.pending_scenario,
                }
                for d in self._devices
            ]
        snap = app_state.simulation.snapshot()
        snap.update(
            {
                "messages_published": self.messages_published,
                "scenarios_generated": self.scenarios_generated,
                "session_id": self._session_id,
                "devices": devices,
            }
        )
        return snap

    # -- internals -----------------------------------------------------------
    def _register_devices(self, count: int) -> List[_DeviceState]:
        now = time.time()
        states: List[_DeviceState] = []
        for index in range(count):
            device_type = DEFAULT_DEVICE_TYPES[index % len(DEFAULT_DEVICE_TYPES)]
            profile = DEVICE_PROFILES[device_type]
            device_id = f"sim-{device_type[:8]}-{index + 1:02d}"
            jitter = self._rng.uniform(0.9, 1.1)
            interval = profile.interval_s * jitter

            with session_scope() as session:
                device_service.upsert_device(
                    session,
                    device_id,
                    name=profile.label,
                    device_type=device_type,
                    status="online",
                    location="simulated",
                    firmware=profile.firmware,
                    is_simulated=True,
                    behaviour_label="normal",
                    expected_interval_s=interval,
                )

            readings = {
                sensor.name: round(
                    min(max(sensor.baseline, sensor.low), sensor.high), 3
                )
                for sensor in profile.sensors
            }
            readings.update({k: round(v, 3) for k, v in profile.fixed.items()})

            states.append(
                _DeviceState(
                    device_id=device_id,
                    device_type=device_type,
                    interval_s=interval,
                    rng=random.Random(self._rng.random()),
                    next_due=now + self._rng.uniform(0.0, interval),
                    readings=readings,
                )
            )
        return states

    def _run(self) -> None:
        while not self._stopping.is_set():
            now = time.time()
            with self._lock:
                states = list(self._devices)
            for state in states:
                if now < state.next_due:
                    continue
                try:
                    self._emit(state, now)
                except Exception as exc:  # pragma: no cover - defensive
                    log.error("Emit failed for %s: %s", state.device_id, exc)
            self._stopping.wait(0.05)

    def _emit(self, state: _DeviceState, now: float) -> None:
        profile = DEVICE_PROFILES[state.device_type]
        rng = state.rng

        blackout = state.blackout_remaining > 0
        flood = state.burst_remaining > 0
        scenario = state.pending_scenario

        if blackout:
            # Silent interval: the device simply does not publish, which is
            # what the detector sees as a telemetry blackout.
            state.blackout_remaining -= 1
            state.next_due = now + state.interval_s
            return

        if scenario is None and not flood and rng.random() < settings.sim_anomaly_probability:
            scenario = rng.choice(SCENARIOS)
            if scenario == "message_flood":
                flood = True
                state.burst_remaining = rng.randint(8, 14)
            elif scenario == "telemetry_blackout":
                state.blackout_remaining = rng.randint(*BLACKOUT_INTERVALS)
                state.next_due = now + state.interval_s
                self.scenarios_generated += 1
                SIM_SCENARIOS_TOTAL.labels(scenario=scenario).inc()
                app_state.simulation.anomalies_injected += 1
                log.info("Ambient scenario %s on %s", scenario, state.device_id)
                return

        readings, status, attempts = self._normal_readings(state, profile, rng)

        if scenario == "sensor_spike":
            readings = self._spike(readings, profile, rng)
            status = "degraded"
        elif scenario == "connection_storm":
            attempts = rng.randint(150, 250)
            status = "degraded"
            if "rssi" in readings:
                readings["rssi"] = round(rng.uniform(-88, -82), 3)
        elif scenario == "unauthorized_access":
            attempts = rng.randint(20, 120)
            if "rssi" in readings:
                readings["rssi"] = round(rng.uniform(-98, -95), 3)
            status = "degraded"
        elif flood:
            state.burst_remaining -= 1

        if scenario is not None:
            state.pending_scenario = None
            self.scenarios_generated += 1
            SIM_SCENARIOS_TOTAL.labels(scenario=scenario).inc()
            app_state.simulation.anomalies_injected += 1
            log.info("Ambient/injected scenario %s on %s", scenario,
                     state.device_id)

        state.status = status
        state.connection_attempts = attempts
        state.readings = readings

        payload_readings = dict(readings)
        if attempts:
            payload_readings["connection_attempts"] = float(attempts)
        payload_readings["reading_count"] = float(len(readings))

        payload = mqtt_publisher.build_telemetry_payload(
            device_id=state.device_id,
            readings=payload_readings,
            status=status,
            is_synthetic=True,
        )
        self._dispatch(state.device_id, payload)

        self.messages_published += 1
        SIM_MESSAGES_TOTAL.inc()

        # Schedule the next tick.
        if flood:
            state.next_due = now + max(0.05, state.interval_s * FLOOD_FACTOR)
        else:
            state.next_due = now + state.interval_s * rng.uniform(0.9, 1.1)

    # -- reading builders ----------------------------------------------------
    def _normal_readings(self, state: _DeviceState, profile, rng: random.Random):
        import math

        now = time.time()
        readings: Dict[str, float] = {}
        for sensor in profile.sensors:
            value = sensor.baseline
            if sensor.drift:
                value += sensor.drift * math.sin(now / 90.0)
            value += rng.gauss(0.0, sensor.spread)
            readings[sensor.name] = round(min(max(value, sensor.low), sensor.high), 3)
        for key, value in profile.fixed.items():
            readings[key] = round(value, 3)

        status = "online"
        attempts = 0
        if rng.random() < 0.02:
            attempts = rng.randint(1, 3)          # routine reconnect
        if rng.random() < 0.012:
            status = "degraded"                   # transient blip
        return readings, status, attempts

    def _spike(self, readings: Dict[str, float], profile, rng: random.Random):
        out = dict(readings)
        sensors = list(profile.sensors)
        if not sensors:
            return out
        for sensor in rng.sample(sensors, k=min(2, len(sensors))):
            direction = rng.choice([-1, 1])
            magnitude = abs(sensor.spread) * rng.uniform(12, 30) + abs(sensor.baseline) * 0.5
            out[sensor.name] = round(sensor.baseline + direction * magnitude, 3)
        return out

    # -- transport dispatch --------------------------------------------------
    def _dispatch(self, device_id: str, payload: Dict[str, Any]) -> None:
        """Publish through the transport, or ingest directly as a last resort."""
        if not mqtt_publisher.publish_telemetry(
            device_id, payload["readings"], status=payload["status"],
            is_synthetic=True,
        ):
            self._ingest_direct(device_id, payload)

    @staticmethod
    def _ingest_direct(device_id: str, payload: Dict[str, Any]) -> None:
        """No transport running: store the record straight into the database."""
        try:
            inbound = TelemetryIn.model_validate(
                {
                    "device_id": device_id,
                    "timestamp": payload.get("timestamp"),
                    "status": payload.get("status", "online"),
                    "readings": payload.get("readings", {}),
                    "is_synthetic": True,
                    "ingest_source": "simulator",
                }
            )
            with session_scope() as session:
                telemetry_service.ingest_telemetry(
                    session, inbound, run_detection=True
                )
        except Exception as exc:  # pragma: no cover - defensive
            log.error("Direct ingest failed for %s: %s", device_id, exc)

    # -- simulation session bookkeeping --------------------------------------
    def _open_session(self, count: int) -> Optional[int]:
        try:
            with session_scope() as session:
                row = SimulationSession(
                    started_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    status="running",
                    scenario="ambient",
                    device_count=count,
                    transport=app_state.mqtt.mode,
                    notes="Started by the device simulator.",
                )
                session.add(row)
                session.flush()
                return row.id
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Could not open simulation session: %s", exc)
            return None

    def _close_session(self) -> None:
        if self._session_id is None:
            return
        try:
            with session_scope() as session:
                row = session.get(SimulationSession, self._session_id)
                if row is not None:
                    row.stopped_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    row.status = "stopped"
                    row.messages_published = self.messages_published
                    row.anomalies_generated = self.scenarios_generated
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Could not close simulation session: %s", exc)


def _default_messages(scenario: str) -> int:
    if scenario == "message_flood":
        return 12
    if scenario == "telemetry_blackout":
        return 8
    if scenario == "connection_storm":
        return 10
    return 6


simulator = DeviceSimulator()
