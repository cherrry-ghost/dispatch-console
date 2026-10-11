# Demo script — how to present this project

Everything below is written as: **what you click** → **what you say**.
Read one line, do it, say the line. That is the whole trick.

The console now has tabs across the top (Overview, Fleet, Operations,
AI Copilot, Media, Audit), so you never scroll more than one screen.

---

## Setup — before the examiner arrives

Open three terminals (PowerShell → `wsl`, or any WSL terminal):

```bash
# terminal 1 - start the API
cd ~/dispatch-console && source .venv/bin/activate
export DATABASE_URL="postgresql://postgres:postgres@localhost/dispatch"
uvicorn main:app --port 8010

# terminal 2 - start the console (the website)
cd ~/dispatch-console/frontend && npm run dev

# terminal 3 - run the one-command proof (fills data + trains the model)
cd ~/dispatch-console && source .venv/bin/activate
export DATABASE_URL="postgresql://postgres:postgres@localhost/dispatch"
python demo.py
```

`demo.py` ends with `ALL 83 CHECKS PASSED`. Say:
> "That was the whole system tested end to end in one command —
> 83 checks, all green. Now let me show it to you live."

Then open **http://localhost:5173** in the browser (Chrome, full screen).

Logins you will type during the demo:
| who | username | password |
|-----|----------|----------|
| dispatcher (main demo) | `dispatcher` | `dispatch123` |
| manager (approvals)    | `manager`    | `manager123` |
| driver (phone app)     | driver #1    | PIN `1234`   |

---

## The tour (about 15 minutes)

### 1. Log in (Overview tab)
- Top right: log in as `dispatcher / dispatch123`.
- **Say:** "Nothing can be written anonymously. Every action needs a
  signed login token, and everything gets recorded with the person's
  name on it."

### 2. Overview tab — the big picture
- Point at the **KPI cards** on top (vehicles, pending shipments, model status).
- Point at the **map**: red dots are locations, lines are shipments.
- Point at **Live tracking** (right): positions arriving over WebSocket.
- Point at **Events** (left of it): geofence crossings, road closures.
- **Say:** "This is the control room. One screen: where everything is,
  what is pending, and what is happening right now."

### 3. Fleet tab
- Show the table: every vehicle, its capacity, and what it is doing.
- **Say:** "Guardrails know each vehicle's capacity and paperwork —
  that is what makes the safety checks possible."

### 4. AI Copilot tab — the honest AI
- Click the suggestion **"How many vehicles are idle?"** → instant answer.
- Click **"Predict delay for shipment 1"** → an XGBoost prediction with
  an honest range and a trust score.
- Click **"Any anomalies?"** → impossible-speed trips flagged for review.
- **Say:** "The copilot is rule-based and deterministic — no black box
  making decisions. It only answers and proposes."

### 5. Copilot proposes, human decides
- Click the suggestion **"Add forbidden zone at lat 13.05 lng 80.25 radius 5"**.
- The copilot shows a yellow **proposal box**. Do NOT click yet — say:
- **Say:** "It did not touch the database. It proposes, a human decides."
- Click **Accept** → "Executed through the guardrails."
- **Say:** "Even after I accept, it goes through the same safety rules
  a human action would. Both the suggestion and my decision are in the
  audit ledger."

### 6. Operations tab — guardrails on a normal assignment
- Assign a shipment: pick any pending shipment, a vehicle and a driver,
  click **Assign**.
- **Say:** "If the driver's insurance were expired or the truck over
  capacity, this button would refuse — permanently. Hard blocks can
  never be stored, not even by the manager."

### 7. Operations tab — manager approval (if a warning shows)
- If a warning-style approval is waiting: log out, log in as
  `manager / manager123`, come back to Operations, add a note, **Approve**.
- **Say:** "Only warnings — like a truck at 96% capacity — wait for the
  manager. The manager is the single point of human judgement."

### 8. Audit tab — the finale
- Click **Refresh** → every action from this demo, each row showing
  *who* did *what*, and whether it was allowed.
- Click **Verify chain** → "Chain intact (N entries checked)".
- **Say:** "Each record's fingerprint includes the previous record's.
  Change one letter anywhere and verification fails. This log cannot
  be quietly edited."

### 9. Media tab (if demo.py ran, checks exist)
- Show verdicts: clean / suspicious / likely_fake, with reasons.
- **Say:** "Photos sent as proof are checked by deterministic forensics
  — hash, camera metadata, tampering analysis, duplicate reuse. It only
  advises; a human still decides."

### 10. Driver phone app
- On a phone (same Wi-Fi): open `http://<your-PC-IP>:8010/driver-app/`
  (or `http://localhost:8010/driver-app/` in a narrow browser window).
- Sign in: driver id `1`, PIN `1234`.
- Show: job list → Deliver → photo proof → incident report.
- **Say:** "The driver app queues deliveries offline and syncs with the
  photo when connection returns. Reporting an incident automatically
  notifies the customer."

### Closing line
> "So: an AI that proposes but never acts alone, guardrails that no one
> — human or AI — can bypass, and a tamper-proof record of everything.
> That is the project."

---

## Likely questions, one-line answers

- **Can the manager override a hard block?** No. Hard blocks never save
  anything; only warnings reach the manager, and approving re-runs the
  same rule check.
- **What stops the AI doing something dangerous?** It never executes
  directly — accepted proposals re-enter the exact same guarded
  endpoints a human uses.
- **How do you know the logs weren't edited?** Each audit row's hash
  includes the previous row's hash; Verify chain recomputes all of them.
- **Is the media check an AI?** No — deterministic forensics
  (hash, EXIF, error-level analysis, duplicate detection), explainable
  by design, advisory only.
- **Where does the trust score come from?** Holdout error, prediction
  interval coverage and drift signals, combined into 0–100
  (`trust.py`).
- **What happens offline for a driver?** The PWA queues the delivery in
  localStorage and syncs when connectivity returns.
