"""Device profiles used by the simulator *and* the synthetic data generator.

Every profile describes the plausible operating envelope of one category of
virtual IoT device.  Values are invented for this demo - they are not
measurements from real hardware - and the CSV they produce is marked
``source=synthetic`` so it can never be mistaken for a real dataset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class SensorProfile:
    name: str
    baseline: float
    spread: float        # 1-sigma noise
    low: float           # plausible operating minimum
    high: float          # plausible operating maximum
    drift: float = 0.0   # slow sine drift amplitude
    unit: str = ""


@dataclass(frozen=True)
class DeviceProfile:
    device_type: str
    label: str
    interval_s: float
    sensors: Tuple[SensorProfile, ...]
    firmware: str = "1.0.0"
    description: str = ""
    # extra readings the device always publishes
    fixed: Dict[str, float] = field(default_factory=dict)


def _s(name, baseline, spread, low, high, unit="", drift=0.0) -> SensorProfile:
    return SensorProfile(name, baseline, spread, low, high, drift, unit)


DEVICE_PROFILES: Dict[str, DeviceProfile] = {
    "temperature_sensor": DeviceProfile(
        device_type="temperature_sensor",
        label="Temperature sensor",
        interval_s=2.0,
        description="Indoor temperature/humidity probe.",
        sensors=(
            _s("temperature", 22.0, 0.6, 15.0, 35.0, "°C", drift=1.5),
            _s("humidity", 48.0, 2.5, 20.0, 90.0, "%", drift=5.0),
        ),
        fixed={"battery_pct": 96.0},
    ),
    "smart_energy_meter": DeviceProfile(
        device_type="smart_energy_meter",
        label="Smart energy meter",
        interval_s=1.5,
        description="Household energy meter reporting power draw.",
        sensors=(
            _s("energy_w", 420.0, 45.0, 50.0, 4000.0, "W", drift=120.0),
            _s("voltage", 230.0, 2.0, 200.0, 250.0, "V"),
            _s("current_a", 1.9, 0.2, 0.1, 18.0, "A"),
        ),
    ),
    "environmental_sensor": DeviceProfile(
        device_type="environmental_sensor",
        label="Environmental monitor",
        interval_s=3.0,
        description="Air-quality / environmental monitoring node.",
        sensors=(
            _s("temperature", 24.0, 0.8, 10.0, 45.0, "°C", drift=2.0),
            _s("humidity", 55.0, 3.0, 15.0, 95.0, "%", drift=6.0),
            _s("rssi", -60.0, 3.0, -95.0, -30.0, "dBm"),
        ),
        fixed={"co2_ppm": 420.0},
    ),
    "smart_home_device": DeviceProfile(
        device_type="smart_home_device",
        label="Smart home device",
        interval_s=2.5,
        description="Connected smart-home actuator (plug / lock / hub).",
        sensors=(
            _s("energy_w", 65.0, 12.0, 0.0, 900.0, "W"),
            _s("voltage", 231.0, 1.8, 205.0, 245.0, "V"),
            _s("rssi", -55.0, 4.0, -90.0, -25.0, "dBm"),
        ),
        fixed={"uptime_h": 72.0},
    ),
    "industrial_sensor": DeviceProfile(
        device_type="industrial_sensor",
        label="Industrial monitor",
        interval_s=1.0,
        description="Industrial vibration / pressure monitor.",
        sensors=(
            _s("temperature", 45.0, 1.8, 10.0, 120.0, "°C", drift=4.0),
            _s("humidity", 40.0, 2.0, 5.0, 95.0, "%"),
            _s("voltage", 24.0, 0.35, 18.0, 30.0, "V"),
            _s("rssi", -65.0, 3.5, -95.0, -30.0, "dBm"),
        ),
        fixed={"vibration_mm_s": 1.2},
    ),
    "camera_gateway": DeviceProfile(
        device_type="camera_gateway",
        label="Camera gateway",
        interval_s=2.0,
        description="Video gateway reporting bandwidth and link quality.",
        sensors=(
            _s("energy_w", 18.0, 3.0, 2.0, 120.0, "W"),
            _s("rssi", -58.0, 4.0, -92.0, -25.0, "dBm"),
            _s("temperature", 38.0, 1.2, 10.0, 85.0, "°C"),
        ),
        fixed={"bitrate_kbps": 2400.0},
    ),
}

DEFAULT_DEVICE_TYPES: List[str] = list(DEVICE_PROFILES.keys())


def get_profile(device_type: str) -> DeviceProfile:
    if device_type in DEVICE_PROFILES:
        return DEVICE_PROFILES[device_type]
    return DEVICE_PROFILES["temperature_sensor"]


def simulate_reading(profile: DeviceProfile, t_seconds: float) -> Dict[str, float]:
    """Produce one plausible reading set for *profile* at time *t_seconds*."""
    import random

    out: Dict[str, float] = {}
    for sensor in profile.sensors:
        value = sensor.baseline
        if sensor.drift:
            import math
            value += sensor.drift * math.sin(t_seconds / 90.0)
        value += random.gauss(0.0, sensor.spread)
        out[sensor.name] = round(min(max(value, sensor.low), sensor.high), 3)
    for key, value in profile.fixed.items():
        out[key] = round(value, 3)
    return out
