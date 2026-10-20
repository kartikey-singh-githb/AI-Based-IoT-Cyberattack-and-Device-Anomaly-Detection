# P310 — AI-Based IoT Cyberattack & Device Anomaly Detection

Full-stack reference implementation: a FastAPI backend that ingests IoT
telemetry (MQTT or a labelled in-process fallback), detects statistically
unusual behaviour with an Isolation Forest, records anomaly events and
security alerts in SQLite, and serves a React dashboard with a built-in
device simulator.

> **What this is, honestly.** Every device in this project is virtual and every
> row of telemetry is simulated (`is_synthetic=True` everywhere). The detector
> reports *statistically unusual* behaviour relative to its training baseline —
> a detection is a lead for investigation, **not** a confirmed attack. The UI,
> the API and the alert text all say so.

## Architecture

```
┌────────────┐   MQTT (or labelled    ┌──────────────────────────────┐
│  simulator │───in-process fallback)─▶│  FastAPI backend             │
│  (virtual  │                         │  ├─ telemetry ingest         │
│  devices)  │                         │  ├─ stream feature extract   │
└────────────┘                         │  ├─ IsolationForest scoring  │
                                       │  ├─ anomaly + alert store    │
┌────────────┐   HTTP /api/*           │  └─ /metrics (Prometheus)    │
│  React     │────────────────────────▶│                              │
│  dashboard │                         └──────────────┬───────────────┘
└────────────┘                                        │
                                       SQLite  ┌──────▼──────┐  ┌──────────┐
                                                │  p310.db    │  │Mosquitto │
                                                └─────────────┘  └──────────┘
```

| Piece | Path | Notes |
|---|---|---|
| API + pipeline | `backend/app/` | FastAPI, SQLAlchemy, scikit-learn |
| Simulator | `backend/app/simulator/` | 6 device profiles, 5 attack scenarios |
| Frontend | `frontend/src/` | React + Vite, pages for overview/devices/telemetry/anomalies/alerts/model/simulation |
| Monitoring | `monitoring/` | Prometheus scrape + alert rules + provisioned Grafana dashboard |
| Datasets | `data/datasets/` | Bundled synthetic sample; public datasets documented, never committed |

## Quick start (native, no Docker)

```bash
# 1. backend
cd backend
python -m venv .venv && .venv\Scripts\activate        # or source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 2. frontend (second terminal)
cd frontend
npm install
npm run dev            # http://127.0.0.1:5173, proxies /api -> :8000
```

No broker? No problem: the backend falls back to an in-process transport and
labels the mode as `fallback` in the UI and `/mqtt/status`. Start the fleet
from the **Simulation** page (or `POST /simulation/start`) and traffic flows
through the real pipeline.

Optional real broker:

```bash
docker compose up mosquitto     # then POST /mqtt/reconnect on the backend
```

## Full stack with Docker

> **Not executed in this environment.** This project was developed and verified
> on Windows **without a Docker daemon available**, so the `docker compose`
> stack below has **not** been run or tested locally — only the native workflow
> above is verified end-to-end. The compose file and Dockerfiles are provided
> and their configuration parses, but treat the containerized path as untested
> until you run it on a machine with Docker.

```bash
docker compose up --build
# backend   http://localhost:8000       (docs at /docs)
# frontend  http://localhost:8080
# grafana   http://localhost:3000       (admin/admin — change it)
# prometheus http://localhost:9090
# mosquitto localhost:1883
```

## Tests

```bash
cd backend
python -m pytest            # 72 tests: API contract, features, detector,
                            # pipeline, simulation, training, analytics
```

Tests run against an isolated temp database and model artifact — they never
touch your real `p310.db` or trained model.

## API surface (summary)

| Area | Endpoints |
|---|---|
| Health | `GET /health`, `/health/live`, `/health/ready`, `/metrics` |
| Devices | `GET/POST /devices`, `GET/PATCH/DELETE /devices/{id}` |
| Telemetry | `GET/POST /telemetry`, `GET /telemetry/latest/{id}`, `/telemetry/stats/{id}` |
| Anomalies | `GET /anomalies`, `/anomalies/counts`, `GET/PATCH /anomalies/{id}` |
| Alerts | `GET/POST /alerts`, `GET /alerts/{id}`, `PATCH /alerts/{id}`, `POST /alerts/{id}/acknowledge\|resolve` |
| Analytics | `GET /analytics/overview\|summary\|performance\|timeline` |
| Model | `GET /model`, `GET /model/history`, `POST /model/train`, `GET /datasets[/{id}]`, `POST /datasets/generate-sample` |
| Simulation | `GET /simulation/status\|devices\|sessions`, `POST /simulation/start\|stop\|inject` |
| MQTT | `GET /mqtt/status`, `POST /mqtt/reconnect\|disconnect` |

Interactive docs: <http://127.0.0.1:8000/docs>

## ML methodology (short version)

* **Feature space (`stream`)**: 33 per-device features computed from a sliding
  window — inter-arrival statistics, burst/stall persistence, missing-message
  ratio, per-channel deviations, status and connection-attempt signals. Only
  information available at message-arrival time (no leakage).
* **Model**: scikit-learn `IsolationForest`. The inference threshold **and**
  the suspicious/critical severity bands are calibrated from quantiles of the
  training score distribution (`P310_ML_FLAG_QUANTILE`), so alert volume is
  predictable.
* **Training data**: generated synthetic telemetry (6 device profiles, 5
  scenario types, ~8 % abnormal). Ground-truth labels are used **only** for
  held-out evaluation metrics — the estimator itself is fitted on the
  unfiltered stream, matching production where labels don't exist.
* **Public datasets** (CICIoT2023, MQTT IoT IDS 2020): documented in
  `data/datasets/README.md`. Because their features are flow statistics the
  live pipeline cannot reproduce, models trained on them live in a separate
  `dataset` feature space and are **refused** for live inference.

Details: `docs/ml_methodology.md`.

## Configuration

All settings use the `P310_` prefix; copy `backend/.env.example` to
`backend/.env` and edit. Notables:

| Variable | Default | Meaning |
|---|---|---|
| `P310_MQTT_HOST/PORT` | `127.0.0.1:1883` | broker; unreachable → labelled fallback |
| `P310_SIM_AUTO_START` | `false` | start the fleet on boot |
| `P310_ML_FLAG_QUANTILE` | `0.05` | expected flag rate; calibrates threshold + bands |
| `P310_FEATURE_WINDOW_SIZE` | `30` | look-back window; must match training |
| `P310_DETECTION_COOLDOWN_S` | `10` | per-device rate limit on persisted events |

## Documentation

* `docs/ml_methodology.md` — features, training, evaluation, caveats
* `docs/architecture.md` — request/data flows, module map
* `data/datasets/README.md` — dataset licensing and loading
* `monitoring/` — Prometheus rules, Grafana dashboard
