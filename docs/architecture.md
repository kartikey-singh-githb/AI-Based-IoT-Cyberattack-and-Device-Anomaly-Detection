# Architecture

## Request/data flows

### Telemetry ingestion (live)

```
device (simulated) 
  -> MQTT publish  [iot/devices/{id}/telemetry]
     (real broker, or the labelled in-process LoopbackTransport when
      no broker is reachable - mode reported as "fallback", never faked)
  -> subscriber handler
  -> telemetry_service.ingest_telemetry()
       sanitise readings -> upsert device -> persist Telemetry row
  -> detection_service.analyse_telemetry()
       rolling per-device history -> extract_stream_features (33 feats)
       -> AnomalyDetector.score_features()
       -> if flagged (and not in cooldown):
            AnomalyEvent (origin="detector") + SecurityAlert (origin="detection")
```

The same path serves `POST /telemetry` (manual ingestion), so tests and
external tooling exercise the identical pipeline.

### Scenario injection (demo)

`POST /simulation/inject` does two deliberately independent things:

1. If the simulator is running, it schedules abnormal telemetry for the
   device so the *model* has a real chance to detect the traffic itself.
2. It records a clearly labelled event/alert (`origin="scenario"`,
   title prefixed `[SIMULATED]`), so a demo never depends on the model
   happening to flag it. Both appear in the feed, distinguishable by
   `origin`.

### Detection vocabulary

| Store | Severities | Origin values |
|---|---|---|
| `anomaly_events` | `suspicious`, `critical` (detector) / mapped for scenarios | `detector`, `scenario` |
| `security_alerts` | `low`, `medium`, `high`, `critical` | `detection`, `scenario`, `manual` |

Alerts created from detector events use `origin="detection"`; the alert text
always includes the "not a confirmation of attack" caveat.

## Module map (backend)

| Module | Responsibility |
|---|---|
| `app/main.py` | FastAPI app factory, lifespan (DB init, model load, background services), metrics middleware, CORS, error handlers |
| `app/runtime.py` | Background service lifecycle; `inject_scenario()` API entry point |
| `app/core/config.py` | `P310_`-prefixed settings (env / `.env`), derived paths |
| `app/core/metrics.py` | Prometheus counters/gauges/histograms; `/metrics` |
| `app/core/state.py` | In-process live state (MQTT link, simulator run, latest-telemetry index) — never persisted |
| `app/db/database.py` | SQLite engine, session scopes, idempotent `init_db`, additive migrations |
| `app/db/models.py` | `Device`, `Telemetry`, `AnomalyEvent`, `SecurityAlert`, `SimulationSession`, `ModelMetadata` |
| `app/mqtt/manager.py` | Transport abstraction: `MqttTransport` (paho) / `LoopbackTransport` (in-process queue); honest mode reporting |
| `app/simulator/device_simulator.py` | Threaded virtual fleet: profiles, jitter, ambient anomalies, scheduled scenarios |
| `app/data/device_profiles.py` | Per-device-type sensor envelopes |
| `app/data/synthetic.py` | Deterministic labelled training-data generator |
| `app/data/dataset_loader.py` | Dataset registry (bundled + public), CSV loading, shape detection |
| `app/ml/feature_engineering.py` | Stream + batch feature extraction (identical space) |
| `app/ml/anomaly_detector.py` | IsolationForest wrapper: train, calibrate threshold/bands, score, explain, save/load |
| `app/ml/train_model.py` | Training pipelines (stream / dataset space), held-out evaluation |
| `app/services/*` | Business logic per domain (telemetry, detection, alerts, analytics, model lifecycle) |
| `app/api/routes/*` | Thin HTTP layer: validation, status codes, response models |

## Frontend

React + TypeScript + Vite. Pages: Overview, Devices, Telemetry, Anomalies,
Alerts, Model, Simulation. `src/api/client.ts` wraps `fetch` with an `/api`
prefix (dev-server proxy → backend:8000; nginx in Docker). `usePolling`
provides interval-based refresh with mount/unmount cleanup.

## Monitoring

* Backend exposes Prometheus text format at `/metrics`.
* `monitoring/prometheus.yml` scrapes it every 5 s;
  `monitoring/alerts.yml` watches pipeline health (backend down, high
  anomaly rate, missing model, fallback active, slow detection).
* Grafana is provisioned (`monitoring/grafana/provisioning`) with a
  dashboard (`p310-overview`) covering ingest rate, detections by severity,
  alert rate, latency percentiles, device health and transport mode.

## Degradation behaviour

Every external dependency degrades explicitly rather than silently:

| Dependency missing | Behaviour |
|---|---|
| MQTT broker | In-process `LoopbackTransport`; `mode="fallback"` in `/mqtt/status`, health, dashboard; telemetry rows get `ingest_source` reflecting the real source |
| Model artifact | Ingestion and storage continue; `/model` reports `trained=false`; no anomaly events are invented |
| Simulator | Stopped by default; API and manual ingestion still work |
| Labels in training data | Training succeeds; `metrics={}` and `metrics_are_measured=false` |

## Security notes

* No authentication is implemented — this is a local demo/reference project.
  Do not expose it to a network without putting auth in front.
* Secrets (MQTT credentials) come from the environment only and are never
  logged (`app/core/logging_config.py` masks them).
* Input validation is strict everywhere: `extra="forbid"` on request models,
  bounded query parameters, sanitised readings (scalars only, size-capped).
* SQLite foreign keys are ON; anomaly→device is not FK-enforced by design
  (events may reference a device that was since deleted), but scenario
  injection validates device existence explicitly.
