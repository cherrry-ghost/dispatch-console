# Smart Logistics Dispatch Console with Guardrails & Audit Trails

Owner: Y. Tinesh (2nd-year AIML student, R.M.D. Engineering College). He is new to Git and the command line, and must submit the project soon. You (Claude Code) build the application; he reviews and approves.

## Working rules (MOST IMPORTANT)

1. Work one phase at a time, following ROADMAP.md. Never start the next phase or the next tier without his explicit "yes".
2. Before starting a phase, say in 3-5 lines what you will build. Then build it. Only stop mid-phase for a real design choice or anything destructive.
3. A phase is done only when all of these are true: pytest passes (paste the real result), the feature works when run, work is committed on its own branch `phase-<id>-<name>`, and merged to main and pushed once he approves.
4. When a phase is done, STOP and give a Phase Report in plain language:
   - What I built (files changed, in simple words)
   - How it works (2-5 sentences, no jargon)
   - Tests: how many pass
   - How to try it (exact PowerShell commands)
   - Anything left open or risky
   - Then ask: "Phase <id> is done. Shall I continue to Phase <next>?" and WAIT.
5. When the last phase of a tier is done, give a Tier Report: what the whole tier added, what the application can do now, what is still missing, and ask: "Tier <n> is complete. Shall I continue to Tier <n+1>?" and WAIT.
6. Do NOT explain commands or code line by line, and give him no exercises. He wants speed. Keep the Phase and Tier Reports plain and short; explain a concept only if he asks.
7. Ask before: dropping or truncating the real `dispatch` database, `git push --force`, deleting files, or installing anything big. Never commit untracked leftovers (for example append_audit.txt) or secrets.

## Environment

- Windows, PowerShell, Python 3.11 with `.venv`, Git, VS Code.
- PostgreSQL 18.6: databases `dispatch` (real) and `dispatch_test` (tests). Connection comes from the `DATABASE_URL` environment variable, set per terminal session.
- Backend: FastAPI + psycopg. Run: `uvicorn main:app --reload --port 8010` (Windows reserves ports 7906-8005 for other things, so 8000 does not work; 8010 is fine).
- Frontend: React 19 + Vite 8 in `frontend/`, dev server on port 5173, Leaflet map. Node 24, npm 11.
- Tests: pytest with the separate test database (pytest.ini, tests/conftest.py). Tables are truncated between tests, so add any new table to the conftest TRUNCATE.
- GitHub repo: cherrry-ghost/dispatch-console (main branch).

## Conventions

- Schema changes go in numbered files under `migrations/` and are run on BOTH `dispatch` and `dispatch_test`.
- Guardrails are deterministic rules in `guardrails.py`, kept separate from any AI. Hard blocks (HTTP 422, nothing saved) can never be overridden; warnings can be sent for manager approval.
- Every important action is written to the hash-chained audit log (`audit.py`, verify with GET /audit/verify). The acting user is hard-coded "dispatcher" until logins exist.
- Keep `requirements.txt` up to date when adding packages.
- Add tests for every new endpoint and rule.

## Current status

- Tier 1 (Phases 0-5A) complete and merged: API, PostgreSQL schema, guardrails (capacity, licence, insurance, fitness, vehicle type, hazmat, near-capacity warning), audit ledger with tamper check, React app with Leaflet map and fleet table, road network with shortest-path routing (networkx).
- Tier 2 in progress. Phase 4C (operations screens: assignment form, approvals, audit viewer) is finished by his account, on branch `phase-4c-operations-screens`, with migration 004 (approvals table) and 45 tests passing. First job: verify with `git status`, commit what is left (leave append_audit.txt alone), run pytest, merge into main and push. Then continue with the next phase in ROADMAP.md.
- Known gaps: road status changes have no API endpoint yet (Phase 6); driver, shipment and location creation are not audited yet.

## Roadmap pointers

- Full roadmap: see ROADMAP.md (he will add it to the repo). Do not invent phases beyond it.
- Known from his plan: Tier 2 includes Phase 6 (road status changes) and sub-phases 7A (synthetic data generator) and 7B (XGBoost delay prediction). Tier 3 goes deeper, Tier 4 is the finished product (Phases up to 12), and fake/real media detection is deliberately last.
- Requirements to honour throughout: guardrails on the AI itself (never route into conflict zones), audit records of every entry by driver, manager, officials and the AI's own suggestions, optional (not mandatory) video proof for road blockages, alternative reliable routes during war or supply-chain disruption.
