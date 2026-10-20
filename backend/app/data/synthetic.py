"""Synthetic telemetry generator.

Produces a clearly-labelled, reproducible dataset of IoT telemetry rows with
injected abnormal behaviours.  Everything written here is **simulated data**:
the CSV carries ``source=synthetic`` and the UI badges it as demo data.

No claim is made that these numbers resemble any real deployment; they exist
so the application (and its tests) can run without downloading a public
dataset.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from app.data.device_profiles import DEVICE_PROFILES, DEFAULT_DEVICE_TYPES

SYNTHETIC_SOURCE = "synthetic"

# Fixed origin used for reproducible generation (see generate_synthetic_telemetry).
DEFAULT_EPOCH = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

SCENARIOS = [
    "message_flood",
    "sensor_spike",
    "telemetry_blackout",
    "connection_storm",
    "unauthorized_access",
]

CSV_COLUMNS = [
    "device_id", "timestamp", "temperature", "humidity", "energy_w",
    "voltage", "current_a", "rssi", "status", "connection_attempts",
    "reading_count", "label", "source",
]


def generate_synthetic_telemetry(
    *,
    n_devices: int = 8,
    rows_per_device: int = 400,
    interval_s: float = 2.0,
    anomaly_ratio: float = 0.08,
    seed: int = 42,
    start: Optional[datetime] = None,
    scenarios: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Return a labelled synthetic telemetry frame (one row per message).

    ``label`` is ``normal`` or one of :data:`SCENARIOS`.  Only
    ``anomaly_ratio`` of the rows are abnormal, mirroring the class imbalance
    of real intrusion-detection data.
    """
    rng = random.Random(seed)
    scenarios = scenarios or SCENARIOS
    # A *fixed* epoch by default: the readings include a deterministic drift
    # term sin(t/90), so a wall-clock start would make the same seed produce
    # different numbers on every run.  Pass ``start`` explicitly when you need
    # timestamps that line up with "now" (the live simulator does this).
    start = start or DEFAULT_EPOCH

    types = DEFAULT_DEVICE_TYPES
    records: List[Dict] = []

    for index in range(n_devices):
        device_type = types[index % len(types)]
        profile = DEVICE_PROFILES[device_type]
        device_id = f"synth-{device_type[:6]}-{index + 1:02d}"
        # devices publish on slightly different phases
        phase = rng.uniform(0, profile.interval_s * 0.4)
        cursor = start.timestamp() + phase
        last_ts = cursor

        labels = _plan_labels(rows_per_device, anomaly_ratio, rng, scenarios)

        for row in range(rows_per_device):
            scenario = labels[row]
            is_anomaly = scenario != "normal"

            # A flood must compress the gap *leading into* this message so the
            # feature row that carries the label is the one that actually
            # exhibits the unusually short inter-arrival time.
            if scenario == "message_flood":
                cursor = last_ts + profile.interval_s * rng.uniform(0.05, 0.15)

            ts = datetime.fromtimestamp(cursor, tz=timezone.utc)

            readings = _readings(profile, t=cursor, seed_rng=rng)
            status = "online"
            connection_attempts = 0

            # ---- natural, non-malicious variation -------------------------
            # A healthy device still jitters its publish interval, drops the
            # odd message and renegotiates a connection now and then.  Without
            # this baseline variation the Isolation Forest would have no
            # normal spread to learn from for the corresponding features.
            if not is_anomaly:
                if rng.random() < 0.02:
                    connection_attempts = rng.randint(1, 3)   # routine reconnect
                if rng.random() < 0.012:
                    status = "degraded"                        # transient blip
                if rng.random() < 0.02:
                    # one naturally missed tick
                    cursor += profile.interval_s
                    ts = datetime.fromtimestamp(cursor, tz=timezone.utc)

            if scenario == "sensor_spike":
                readings = _spike(readings, profile, rng)
                status = "degraded"
            elif scenario == "connection_storm":
                connection_attempts = rng.randint(40, 250)
                status = "degraded" if row % 2 else "online"
            elif scenario == "unauthorized_access":
                connection_attempts = rng.randint(20, 120)
                if "rssi" in readings:
                    readings["rssi"] = round(rng.uniform(-98, -95), 3)
                status = "maintenance" if row % 3 == 0 else "online"
            elif scenario == "message_flood":
                # Compress the gap: the next few rows arrive almost together
                # and are produced by the caller advancing time only slightly.
                pass
            elif scenario == "telemetry_blackout":
                # Skip ahead several intervals for this device.
                cursor += profile.interval_s * rng.randint(4, 9)
                ts = datetime.fromtimestamp(cursor, tz=timezone.utc)

            record = {
                "device_id": device_id,
                "timestamp": ts.isoformat(),
                "status": status,
                "connection_attempts": connection_attempts,
                "reading_count": len(readings),
                "label": scenario if is_anomaly else "normal",
                "source": SYNTHETIC_SOURCE,
            }
            for channel in ("temperature", "humidity", "energy_w",
                            "voltage", "current_a", "rssi"):
                record[channel] = readings.get(channel)
            records.append(record)

            step = profile.interval_s
            if scenario != "message_flood":
                # +/- 15% timing jitter (flood rows already set the cursor)
                step *= rng.uniform(0.85, 1.15)
            cursor += step
            last_ts = ts.timestamp()

    frame = pd.DataFrame.from_records(records, columns=CSV_COLUMNS)
    return frame


def _plan_labels(rows: int, anomaly_ratio: float, rng: random.Random,
                 scenarios: List[str]) -> List[str]:
    """Assign one label per row, keeping the overall abnormal share near
    ``anomaly_ratio``.

    ``message_flood`` is planned as *episodes* of consecutive fast messages
    rather than isolated points: a real message-rate attack is sustained, and
    a single short gap cannot move any windowed rate statistic.
    """
    labels = ["normal"] * rows
    others = [s for s in scenarios if s != "message_flood"]
    wants_flood = "message_flood" in scenarios and rows >= 40

    if wants_flood:
        episode_len = rng.randint(10, 16)
        episodes = 2 if rows * anomaly_ratio >= 40 else 1
        placed = 0
        attempts = 0
        while placed < episodes and attempts < 30:
            attempts += 1
            start_row = rng.randint(0, max(0, rows - episode_len - 1))
            span = range(start_row, min(rows, start_row + episode_len))
            if any(labels[i] != "normal" for i in span):
                continue
            for i in span:
                labels[i] = "message_flood"
            placed += 1

    target = int(rows * anomaly_ratio)
    abnormal = sum(1 for lbl in labels if lbl != "normal")
    if others:
        candidates = [i for i, lbl in enumerate(labels) if lbl == "normal"]
        rng.shuffle(candidates)
        for i in candidates:
            if abnormal >= target:
                break
            labels[i] = rng.choice(others)
            abnormal += 1

    return labels


def _readings(profile, t: float, seed_rng: random.Random) -> Dict[str, float]:
    import math as _math

    out: Dict[str, float] = {}
    for sensor in profile.sensors:
        value = sensor.baseline
        if sensor.drift:
            value += sensor.drift * _math.sin(t / 90.0)
        value += seed_rng.gauss(0.0, sensor.spread)
        out[sensor.name] = round(min(max(value, sensor.low), sensor.high), 3)
    for key, value in profile.fixed.items():
        out[key] = round(value, 3)
    return out


def _spike(readings: Dict[str, float], profile, rng: random.Random) -> Dict[str, float]:
    """Push one or two sensors far outside their plausible envelope."""
    out = dict(readings)
    if not profile.sensors:
        return out
    targets = rng.sample(list(profile.sensors), k=min(2, len(profile.sensors)))
    for sensor in targets:
        direction = rng.choice([-1, 1])
        magnitude = abs(sensor.spread) * rng.uniform(12, 30) + abs(sensor.baseline) * 0.5
        out[sensor.name] = round(sensor.baseline + direction * magnitude, 3)
    return out


def write_sample_csv(path: Path, **kwargs) -> Path:
    """Generate and write the bundled synthetic sample dataset."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = generate_synthetic_telemetry(**kwargs)
    frame.to_csv(path, index=False)
    return path


def load_sample_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(Path(path))
    if "source" in frame.columns:
        # normalise so downstream filters behave predictably
        frame["source"] = frame["source"].fillna(SYNTHETIC_SOURCE)
    return frame


def label_distribution(frame: pd.DataFrame) -> Dict[str, int]:
    if "label" not in frame.columns:
        return {}
    return {str(k): int(v) for k, v in frame["label"].value_counts().items()}
