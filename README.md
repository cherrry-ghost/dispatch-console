# Smart Logistics Dispatch Console

A logistics dispatch system built around one idea: **the AI proposes, humans decide, and nothing happens without a rule check or leaving a trace.**

Built as a final-year-style project: FastAPI + PostgreSQL backend, React console with a live map, a driver PWA, and an end-to-end demo script that verifies the whole application in one command.

## The three pillars

| Pillar | What it means in code |
|---|---|
| **Deterministic guardrails** | `guardrails.py` — capacity, licence, insurance, fitness, vehicle type, hazmat rules. Hard blocks (HTTP 422) can never be overridden; warnings go to a manager for approval. Routing never plans through forbidden (conflict) zones. |
| **Tamper-proof audit ledger** | `audit.py` — every important action is a hash-chained row (each row's hash covers the previous one). `GET /audit/verify` proves no row was edited or removed. Every entry names its actor: dispatcher, manager, driver, or one of the AI components. |
| **Human-in-the-loop AI** | The planner, delay model (XGBoost with honest prediction ranges), anomaly detector, copilot and media checker all *suggest*. The copilot's action proposals are stored in a decision ledger and only execute when a human accepts — and then they re-run through the same guardrails. |

## Feature map (by phase)

- **Core API + schema** — locations, roads (shortest-path routing on a real network), vehicles, drivers, shipments.
- **Guardrails + approvals** — hard blocks and manager-approved warnings.
- **Audit ledger** — hash chain, verify endpoint, full actor history.
- **React console** — Leaflet map, fleet table, assignment form, approvals, audit viewer.
- **Synthetic data generator** — a realistic world (locations, roads, fleet, history) for demos and training.
- **Delay prediction** — XGBoost model with prediction intervals; ETA endpoint with trust scores.
- **Trust & drift monitoring** — `GET /model/health` reports holdout error, interval coverage, drift detection and trust scores for both the ETA model and the planner.
- **Live tracking** — vehicles report positions (REST or WebSocket), consoles watch fleets in real time, geofence zones raise enter/exit alerts.
- **Event pipeline + geofencing** — every notable occurrence flows through an event bus (`/events`, `/ws/events`); alert zones warn without blocking; forbidden zones reroute traffic.
- **Driver PWA** — installable app at `/driver-app/`: job list, proof-of-delivery photos (stored with SHA-256), offline queue that syncs when reconnected, incident reporting with automatic customer notification.
- **Customer notifications** — assigned / problem / delivered updates per shipment.
- **Guardrailed AI copilot** — plain-language questions and action proposals (`POST /copilot/ask`), decision ledger, accept/reject with guardrail re-check.
- **Security** — PBKDF2-hashed logins with roles (admin / dispatcher / manager), signed tokens, driver PIN logins scoped to their own jobs, every audit row carries the real username.
- **Observability & DevOps** — `/health`, `/metrics` (Prometheus format), request logging, Dockerfile + docker-compose (DB + API + console), GitHub Actions CI running the full test suite.
- **Media authenticity** — every uploaded photo gets an explainable verdict: hash re-verification, duplicate-reuse detection, EXIF camera metadata, Error Level Analysis; videos get container checks. `GET /media/checks` lists verdicts; the copilot can quote them.

## Running it

Backend (PowerShell, from the repo root):

```powershell
$env:DATABASE_URL = "postgresql://postgres:postgres@localhost/dispatch"
.venv\Scripts\Activate.ps1
uvicorn main:app --reload --port 8010
```

Console (second terminal):

```powershell
cd frontend
npm run dev          # http://localhost:5173
```

End-to-end demo (third terminal, or after starting the server):

```powershell
$env:DATABASE_URL = "postgresql://postgres:postgres@localhost/dispatch"
python demo.py       # runs 80+ checks against the live server
```

Tests (uses a separate `dispatch_test` database):

```powershell
$env:DATABASE_URL = "postgresql://postgres:postgres@localhost/dispatch"
python -m pytest tests/ -q
```

Everything in Docker: `docker compose up --build` (API on 8010, console on 8080).

## Demo logins (change in production)

| User | Password | Can do |
|---|---|---|
| `admin` | `admin123` | everything |
| `dispatcher` | `dispatch123` | daily operations (assignments, roads, zones, copilot) |
| `manager` | `manager123` | approvals and incident verify/dismiss |
| drivers | PIN `1234` | their own jobs in the driver PWA |

## Architecture in one picture

```
 React console ─┐                          ┌─ planner.py ── guardrails ─┐
 Driver PWA ────┼─► FastAPI (main.py) ─────┼─ copilot.py ───────────────┼─► PostgreSQL
 GPS pushes ────┘        │                 ├─ delay_model (XGBoost)      │    (audit ledger,
                         │                 ├─ events.py  (event bus)     │     events, media
                         ├─ media.py (photo/video forensics)             │     checks, ...)
                         ├─ audit.py (hash chain)                        │
                         └─ security.py (logins, tokens)                 │
                                   │                                     │
                          WebSockets: /ws/live, /ws/events, /ws/vehicle ─┘
```

## Key endpoints

- `POST /assignments` — guardrail-checked assignment (warnings → manager approval)
- `POST /planner/propose` — AI plan proposal (never executes by itself)
- `POST /copilot/ask` — ask or propose; `POST /copilot/proposals/{id}/decision` — human verdict
- `GET /audit`, `GET /audit/verify` — ledger + tamper check
- `POST /incidents`, `.../verify`, `.../resolve` — incident lifecycle closes/reopens roads
- `POST /vehicles/{id}/position`, `WS /ws/live` — live tracking and geofence alerts
- `GET /events`, `WS /ws/events` — the event pipeline
- `POST /model/train`, `GET /model/health` — training, drift, trust
- `GET /media/checks`, `POST /media/check/{kind}/{id}` — media authenticity
- `/health`, `/metrics`, `/docs` — operations

See `docs/API_ENDPOINTS.md` for the full list and `docs/DEMO_SCRIPT.md` for a guided walkthrough.
