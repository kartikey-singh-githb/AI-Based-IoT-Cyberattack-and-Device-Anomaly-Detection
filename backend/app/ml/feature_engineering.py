"""Feature extraction for IoT telemetry.

Two entry points exist:

``extract_stream_features``   online / streaming path.  Takes the rolling
                              history of one device plus the newest message
                              and returns a fixed-length feature dict matching
                              :data:`FEATURE_NAMES`.

``extract_dataset_features`` batch path.  Takes a :class:`pandas.DataFrame`
                              of telemetry-like rows (device_id, timestamp and
                              sensor columns) and produces the *same* feature
                              space, so a model trained offline can be used by
                              the live detector.

The feature space deliberately contains only information available *at the
moment the message arrives* - no future values, no labels - to avoid data
leakage.  See docs/ml_methodology.md.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

SENSOR_CHANNELS: List[str] = [
    "temperature", "humidity", "energy_w", "voltage", "current_a", "rssi",
]

BASE_FEATURES: List[str] = [
    "message_rate",            # messages per second inside the window
    "inter_arrival_mean",      # mean gap between messages (seconds)
    "inter_arrival_std",       # std of gaps
    "inter_arrival_max",       # max gap in window
    "last_gap",                # gap since the previous message
    "gap_ratio",               # last_gap / expected interval
    "gap_z",                    # (last_gap - window mean gap) / window std
    "is_burst",                 # 1 when the gap is under half the expected interval
    "is_stall",                 # 1 when the gap is over twice the expected interval
    "trailing_burst_ratio",     # share of the last 6 gaps that were bursts
    "trailing_stall_ratio",     # share of the last 6 gaps that were stalls
    "missing_message_ratio",   # expected-but-absent ticks in the window
    "sensor_coverage",         # fraction of sensor channels populated
    "reading_count",           # number of readings in the newest payload
    "status_change_rate",      # status flips per message in the window
    "status_degraded",         # 1 when the device reports a non-nominal status
    "status_changed",          # 1 when status differs from the previous message
    "connection_attempts",     # reported connection/auth attempts
    "connection_attempt_rate", # attempts per second inside the window
    "max_sensor_delta",        # largest change vs the previous message
    "payload_bytes",           # serialised payload size (scaled later)
]

CHANNEL_FEATURES: List[str] = []
for _c in SENSOR_CHANNELS:
    CHANNEL_FEATURES.append(_c)            # current value
    CHANNEL_FEATURES.append(f"{_c}_dev")   # deviation from window mean

FEATURE_NAMES: List[str] = BASE_FEATURES + CHANNEL_FEATURES

FEATURE_DESCRIPTIONS: Dict[str, str] = {
    "message_rate": "Messages per second observed in the sliding window.",
    "inter_arrival_mean": "Mean time between consecutive messages (s).",
    "inter_arrival_std": "Standard deviation of inter-message times (s).",
    "inter_arrival_max": "Largest gap inside the window (s).",
    "last_gap": "Seconds since the previous message.",
    "gap_ratio": "last_gap divided by the device's expected interval.",
    "gap_z": "Standardised deviation of the latest gap from the window mean gap.",
    "is_burst": "1 when the latest gap is < 50% of the expected interval.",
    "is_stall": "1 when the latest gap is > 200% of the expected interval.",
    "trailing_burst_ratio": "Share of the last 6 gaps that were bursts (persistence of a flood).",
    "trailing_stall_ratio": "Share of the last 6 gaps that were stalls (persistence of a blackout).",
    "missing_message_ratio": "Share of expected ticks that did not arrive.",
    "sensor_coverage": "Fraction of known sensor channels present in the payload.",
    "reading_count": "Number of key/value readings in the newest payload.",
    "status_change_rate": "Device status changes per message in the window.",
    "status_degraded": "1 when the newest status is degraded/maintenance, else 0.",
    "status_changed": "1 when the newest status differs from the previous one.",
    "connection_attempts": "Connection/authentication attempts reported in the payload.",
    "connection_attempt_rate": "Reported connection attempts per second over the window.",
    "max_sensor_delta": "Largest absolute change in any sensor channel since the previous message.",
    "payload_bytes": "Serialised size of the newest payload in bytes.",
}

for _c in SENSOR_CHANNELS:
    FEATURE_DESCRIPTIONS[_c] = f"Most recent {_c} reading (0 when absent)."
    FEATURE_DESCRIPTIONS[f"{_c}_dev"] = (
        f"({_c} - window mean) / window std; 0 when the window has no spread."
    )


# ---------------------------------------------------------------------------
# Online / streaming extraction
# ---------------------------------------------------------------------------
def extract_stream_features(
    history: Sequence[Dict[str, Any]],
    newest: Dict[str, Any],
    *,
    expected_interval: float = 2.0,
    window_seconds: float = 60.0,
) -> Dict[str, float]:
    """Compute the feature dict for one device.

    Parameters
    ----------
    history:
        Previous messages for this device, each with ``ts`` (epoch seconds)
        and optionally ``readings`` / ``status``.  Must be sorted ascending
        and must NOT include *newest*.
    newest:
        The message currently being processed (same keys).
    expected_interval:
        Device's configured publish interval in seconds.
    window_seconds:
        Look-back window used for rate/statistics features.
    """
    newest_ts: float = float(newest["ts"])
    newest_readings: Dict[str, Any] = dict(newest.get("readings") or {})
    expected = max(float(expected_interval or 0.0), 1e-6)

    # ---- window selection -------------------------------------------------
    cutoff = newest_ts - window_seconds
    window = [m for m in history if float(m["ts"]) >= cutoff]
    window = list(window) + [newest]

    # ---- gaps -------------------------------------------------------------
    timestamps = [float(m["ts"]) for m in window]
    gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
    last_gap = gaps[-1] if gaps else 0.0
    # gaps before the newest message describe history only
    hist_gaps = gaps[:-1] if len(gaps) > 1 else gaps

    span = window_seconds
    message_rate = (len(window) - 1) / span if span > 0 else 0.0

    if hist_gaps:
        gap_mean = float(np.mean(hist_gaps))
        gap_std = float(np.std(hist_gaps))
        gap_max = float(np.max(hist_gaps))
    else:
        gap_mean = last_gap
        gap_std = 0.0
        gap_max = last_gap

    # ---- missing messages -------------------------------------------------
    expected_ticks = max(1.0, window_seconds / expected)
    actual_ticks = float(len(window))
    missing_ratio = max(0.0, min(1.0, 1.0 - actual_ticks / expected_ticks))

    # Persistence of burst/stall behaviour over the tail of the window: a
    # sustained flood or blackout shows up here even after the window mean
    # has adapted to it.
    tail = gaps[-6:] if gaps else []
    if tail:
        trailing_burst_ratio = sum(1 for g in tail if g < 0.5 * expected) / len(tail)
        trailing_stall_ratio = sum(1 for g in tail if g > 2.0 * expected) / len(tail)
    else:
        trailing_burst_ratio = 0.0
        trailing_stall_ratio = 0.0

    # ---- status / connection ---------------------------------------------
    statuses = [str(m.get("status") or "online") for m in window]
    status_changes = sum(
        1 for a, b in zip(statuses, statuses[1:]) if a != b
    )
    status_change_rate = status_changes / max(1, len(statuses) - 1)

    # Connection attempts are taken from the newest message plus a *short*
    # trailing window.  Summing over the whole look-back window would smear a
    # single anomalous burst across every subsequent healthy message.
    newest_attempts = _numeric(newest_readings.get("connection_attempts"))
    recent = window[-5:]
    recent_attempts = sum(
        _numeric((m.get("readings") or {}).get("connection_attempts"))
        for m in recent
    )
    if len(recent) > 1:
        recent_span = max(float(recent[-1]["ts"]) - float(recent[0]["ts"]), 1e-6)
    else:
        recent_span = float(window_seconds)
    connection_attempt_rate = recent_attempts / recent_span

    status_flags = {"degraded", "maintenance", "error", "fault", "offline",
                    "auth_failure", "unreachable"}
    newest_status = str(newest.get("status") or "online").lower()
    prev_status = str(window[-2].get("status") or "online").lower() \
        if len(window) > 1 else newest_status
    status_degraded = 1.0 if newest_status in status_flags else 0.0
    status_changed = 1.0 if newest_status != prev_status else 0.0

    # Largest change per channel since the previous message (raw units; each
    # device is compared only against its own history).
    max_sensor_delta = 0.0
    prev_readings = window[-2].get("readings") or {} if len(window) > 1 else {}
    for channel in SENSOR_CHANNELS:
        new_v = _numeric_opt(newest_readings.get(channel))
        old_v = _numeric_opt(prev_readings.get(channel))
        if new_v is not None and old_v is not None:
            max_sensor_delta = max(max_sensor_delta, abs(new_v - old_v))

    # ---- payload ----------------------------------------------------------
    reading_count = len(newest_readings)
    payload_bytes = len(str(newest.get("payload_raw") or
                            newest.get("readings_json") or
                            _approx_payload(newest_readings)))

    # ---- sensors ----------------------------------------------------------
    present = 0
    values: Dict[str, float] = {}
    devs: Dict[str, float] = {}
    for channel in SENSOR_CHANNELS:
        raw = _numeric_opt(newest_readings.get(channel))
        if raw is None:
            raw = _numeric_opt(newest.get(channel))
        if raw is not None:
            present += 1
        values[channel] = float(raw) if raw is not None else 0.0

        hist_vals = [
            _numeric_opt((m.get("readings") or {}).get(channel))
            for m in window
        ]
        hist_vals = [h for h in hist_vals if h is not None]
        if raw is not None:
            hist_vals.append(raw)
        if len(hist_vals) >= 2:
            mu = float(np.mean(hist_vals))
            sd = float(np.std(hist_vals))
            devs[channel] = (values[channel] - mu) / sd if sd > 1e-9 else 0.0
        else:
            devs[channel] = 0.0

    coverage = present / len(SENSOR_CHANNELS)

    features: Dict[str, float] = {
        "message_rate": message_rate,
        "inter_arrival_mean": gap_mean,
        "inter_arrival_std": gap_std,
        "inter_arrival_max": gap_max,
        "last_gap": last_gap,
        "gap_ratio": last_gap / expected,
        "gap_z": ((last_gap - gap_mean) / gap_std) if gap_std > 1e-9 else 0.0,
        "is_burst": 1.0 if last_gap < 0.5 * expected else 0.0,
        "is_stall": 1.0 if last_gap > 2.0 * expected else 0.0,
        "trailing_burst_ratio": trailing_burst_ratio,
        "trailing_stall_ratio": trailing_stall_ratio,
        "missing_message_ratio": missing_ratio,
        "sensor_coverage": coverage,
        "reading_count": float(reading_count),
        "status_change_rate": status_change_rate,
        "status_degraded": status_degraded,
        "status_changed": status_changed,
        "connection_attempts": newest_attempts,
        "connection_attempt_rate": connection_attempt_rate,
        "max_sensor_delta": max_sensor_delta,
        "payload_bytes": float(payload_bytes),
    }
    for channel in SENSOR_CHANNELS:
        features[channel] = values[channel]
        features[f"{channel}_dev"] = devs[channel]

    return _sanitise(features)


def _approx_payload(readings: Dict[str, Any]) -> str:
    return ",".join(f"{k}={v}" for k, v in readings.items())


# ---------------------------------------------------------------------------
# Batch extraction (training / offline scoring)
# ---------------------------------------------------------------------------
def extract_dataset_features(
    df: pd.DataFrame,
    *,
    expected_interval: float = 2.0,
    window_seconds: float = 60.0,
    timestamp_col: str = "timestamp",
    device_col: str = "device_id",
) -> pd.DataFrame:
    """Turn a telemetry table into the streaming feature space.

    Accepts optional sensor columns; missing columns are treated as absent
    readings rather than an error, which lets the same function handle both
    the synthetic sample and imported device logs.
    """
    if df.empty:
        return pd.DataFrame(columns=FEATURE_NAMES)

    data = df.copy()
    original_index = data.index
    data = data.reset_index(drop=True)

    if timestamp_col in data.columns:
        ts = pd.to_datetime(data[timestamp_col], utc=True, errors="coerce")
        if ts.isna().all():
            ts = pd.RangeIndex(len(data)).astype(float) * expected_interval
            data["_ts"] = ts.astype(float)
        else:
            data["_ts"] = ts.astype("int64") / 1e9
    else:
        data["_ts"] = np.arange(len(data), dtype=float) * expected_interval

    if device_col in data.columns:
        devices = data[device_col].astype(str)
    else:
        devices = pd.Series(["device"] * len(data), index=data.index)

    numeric_map = {
        "temperature": "temperature",
        "humidity": "humidity",
        "energy_w": "energy_w",
        "power_w": "energy_w",
        "voltage": "voltage",
        "current_a": "current_a",
        "rssi": "rssi",
    }
    for src, dst in numeric_map.items():
        if src in data.columns and dst not in data.columns:
            data[dst] = pd.to_numeric(data[src], errors="coerce")

    for channel in SENSOR_CHANNELS:
        if channel not in data.columns:
            data[channel] = np.nan

    if "status" not in data.columns:
        data["status"] = "online"

    # Extra payload fields the streaming extractor understands but which are
    # not part of the sensor channel list.
    passthrough = [
        c for c in ("connection_attempts", "reading_count", "payload_bytes")
        if c in data.columns
    ]

    # ``groupby`` yields one device at a time, so rows are produced in
    # device-grouped order.  Track the original position of every row and
    # restore the caller's ordering at the end - otherwise downstream label
    # vectors (which follow the input frame) would be misaligned with X.
    collected: List[tuple[int, Dict[str, float]]] = []

    for device_id, group in data.groupby(devices, sort=False):
        group = group.sort_values("_ts")
        positions = group.index.to_numpy()

        # Derive this device's expected publish interval from its own gaps so
        # gap_ratio / missing_message_ratio are device-relative, not biased by
        # the caller's default.
        device_interval = expected_interval
        if len(group) > 4:
            deltas = np.diff(group["_ts"].to_numpy(dtype=float))
            deltas = deltas[deltas > 0]
            if deltas.size >= 4:
                median_gap = float(np.median(deltas))
                if 1e-3 < median_gap < 3600:
                    device_interval = median_gap

        history: List[Dict[str, Any]] = []
        for position, (_, record) in zip(positions, group.iterrows()):
            readings = {
                ch: (None if pd.isna(record[ch]) else float(record[ch]))
                for ch in SENSOR_CHANNELS
            }
            readings.update({
                c: (None if pd.isna(record[c]) else float(record[c]))
                for c in passthrough
            })
            readings = {k: v for k, v in readings.items() if v is not None}
            item = {
                "ts": float(record["_ts"]),
                "readings": readings,
                "status": str(record.get("status") or "online"),
            }
            features = extract_stream_features(
                history, item,
                expected_interval=device_interval,
                window_seconds=window_seconds,
            )
            # Honour declared payload size when the source provides it.
            if "reading_count" in passthrough and "reading_count" in readings:
                features["reading_count"] = float(readings["reading_count"])
            if "payload_bytes" in passthrough and "payload_bytes" in readings:
                features["payload_bytes"] = float(readings["payload_bytes"])
            collected.append((int(position), features))
            history.append(item)
            if len(history) > 500:  # bound memory for large files
                del history[:250]

    collected.sort(key=lambda pair: pair[0])
    out = pd.DataFrame(
        [features for _, features in collected], columns=FEATURE_NAMES
    )
    if len(out) == len(data):
        out.index = original_index
    return out


def features_to_matrix(features: Dict[str, float]) -> np.ndarray:
    """Ordered float32 vector matching :data:`FEATURE_NAMES`."""
    return np.array(
        [[float(features.get(name, 0.0)) for name in FEATURE_NAMES]],
        dtype=np.float32,
    )


def top_deviations(
    features: Dict[str, float],
    baselines: Dict[str, Dict[str, float]],
    *,
    top_n: int = 5,
) -> Dict[str, float]:
    """Rank features by |z-score| against the trained baseline.

    ``baselines`` maps feature name -> {"mean": .., "std": ..}.  Features
    without a baseline are skipped.  Used to explain *why* a record was
    flagged, not to decide the flag itself.
    """
    scored: List[tuple[float, str]] = []
    for name in FEATURE_NAMES:
        base = baselines.get(name)
        if not base:
            continue
        mean = float(base.get("mean", 0.0))
        std = float(base.get("std", 0.0))
        if std <= 1e-9:
            continue
        z = abs(float(features.get(name, 0.0)) - mean) / std
        scored.append((z, name))
    scored.sort(reverse=True)
    return {name: round(z, 3) for z, name in scored[:top_n]}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _numeric(value: Any) -> float:
    v = _numeric_opt(value)
    return v if v is not None else 0.0


def _numeric_opt(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _sanitise(features: Dict[str, float]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for key, value in features.items():
        if value is None or (isinstance(value, float) and
                             (math.isnan(value) or math.isinf(value))):
            out[key] = 0.0
        else:
            out[key] = float(value)
    return out


def describe_features(names: Optional[Iterable[str]] = None) -> Dict[str, str]:
    names = list(names) if names is not None else FEATURE_NAMES
    return {n: FEATURE_DESCRIPTIONS.get(n, "") for n in names}
