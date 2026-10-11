# Guided demo script (viva walkthrough)

`python demo.py` runs the whole application end to end and prints `OK`/`FAIL`
for every step. This doc maps each section to what to say while it runs.

## Setup (before the examiner arrives)

```powershell
# terminal 1 - API
$env:DATABASE_URL = "postgresql://postgres:postgres@localhost/dispatch"
.venv\Scripts\Activate.ps1
uvicorn main:app --port 8010

# terminal 2 - console
cd frontend ; npm run dev
```

Then run `python demo.py` in a third terminal, or walk the sections below by
hand in the browser + Swagger (`http://localhost:8010/docs`).

Login as `dispatcher / dispatch123` in the console, `manager / manager123`
when an approval is needed.

## Sections and talking points

0. **Login** — "No anonymous actions. Every write needs a signed token, and
   the audit ledger records *who* did it."
1. **Server health** — trivial liveness check.
2. **Data generator** — a reproducible synthetic world: depots, roads,
   fleet, shipments and months of delivery history for the ML model.
3. **AI capacity planner** — proposes where every pending shipment should
   go, with a trust score. Watch: it never writes anything itself.
4. **Guardrails + manager approval** — clean assignment goes straight
   through; a warning case (near capacity) is parked for the manager; hard
   blocks (over capacity, expired insurance) are refused with 422 and
   *cannot* be overridden by anyone.
5. **Forbidden zone** — draw a conflict zone over the map: routing
   automatically goes around it; turn it off and the direct road returns.
6. **Ranked routes** — alternatives labelled safe / caution /
   needs_approval; using a risky route requires a manager decision.
7. **Incident lifecycle** — report → manager verifies (road closes,
   dispatchers see it) → resolve (road reopens). Evidence files are stored
   with a SHA-256 fingerprint.
8. **Copilot** — ask questions in plain English; ask it to close a road:
   it only *proposes*; a human accepts or rejects; both sides are audited.
9. **Model training** — XGBoost on the generated history; honest
   prediction ranges; holdout error reported.
10. **ETA with trust** — every prediction carries a range and a trust
    score; `GET /model/health` shows drift detection (plant a drift and
    watch trust drop).
11-12. **Anomalies** — impossible-speed and impossible-duration trips are
    flagged for review.
13-14. **Live tracking & events** — push positions; consoles receive them
    live over WebSocket; crossing a geofence alerts in real time; road
    closures flow through the event pipeline. Alert zones warn but never
    block; forbidden zones block.
16-17. **Driver PWA** — open `http://localhost:8010/driver-app/` on a
    phone, sign in with driver id + PIN 1234: job list, delivery with
    optional photo proof, offline queue, incident reporting that notifies
    the customer automatically.
19. **Copilot decision ledger** — every suggestion and every human
    decision is stored and queryable.
20. **Security & observability** — `/health` (DB + model + network),
    `/metrics` (Prometheus format), audit rows carrying real usernames.
21. **Media authenticity** — a camera-style photo passes clean; the *same*
    photo submitted again for a different incident is rejected as
    likely_fake (duplicate reuse); the copilot can quote the verdict.
22. **Audit ledger** — the finale: `GET /audit/verify` walks the whole
    hash chain and proves nothing was tampered with.

## Likely examiner questions (short answers)

- **Can a manager override a hard block?** No. Hard blocks never save
  anything; only *warnings* go to approvals, and the approval applies the
  same rule check again.
- **What stops the AI from doing something dangerous?** The copilot and
  planner never execute: accept/re-assign actions re-enter the same
  guarded endpoints a human uses, so the guardrails apply twice.
- **How do you know the logs weren't edited?** Each audit row's hash
  includes the previous row's hash; `/audit/verify` recomputes the chain.
- **Is the media check an AI?** It is deterministic forensics (hash, EXIF,
  error-level analysis, duplicate detection) — explainable by design — and
  it only advises; a human decides.
- **What happens offline for a driver?** The PWA queues the delivery in
  localStorage and syncs (with the photo) when connectivity returns.
- **Where is the "trust" number from?** Holdout error, interval coverage
  and drift signals are combined into a 0-100 score for the ETA model and
  the planner (see `trust.py`).
