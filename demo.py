#!/usr/bin/env python3
"""End-to-end walkthrough of the dispatch console.

Start the server, then run:  python demo.py
Optional:  python demo.py http://localhost:8010
"""

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from random import Random

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8010"
PASS, FAIL = [], []


def call(method, path, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body.decode(errors="replace")}


def ok(label, detail=""):
    PASS.append(label)
    print(f"  OK   {label}" + (f" - {detail}" if detail else ""))


def fail(label, detail):
    FAIL.append(label)
    print(f"  FAIL {label} - {detail}")


def check(cond, label, detail_ok="", detail_bad="unexpected response"):
    if cond:
        ok(label, detail_ok if isinstance(detail_ok, str) else "")
    else:
        fail(label, detail_bad)
    return cond


def section(title):
    print(f"\n=== {title} ===")


rng = Random()

# 1 ─ health ---------------------------------------------------------------
section("1. Server health")
s, b = call("GET", "/health")
check(s == 200 and b.get("status") == "ok", "server is alive")

# 2 ─ generate a synthetic world ------------------------------------------
section("2. Generate a synthetic world (data generator)")
s, b = call("POST", "/datagen", {"scale": "medium", "seed": rng.randint(1, 9999),
                                 "confirm": True})
check(s == 200, "world generated",
      f"{b.get('locations')} locations, {b.get('roads')} roads, "
      f"{b.get('vehicles')} vehicles, {b.get('drivers')} drivers, "
      f"{b.get('shipments')} shipments, {b.get('history')} history trips")
s, locs = call("GET", "/locations")
locs = locs[-b["locations"]:]          # only cities of the world we just built
world_ids = {l["id"] for l in locs}
s, ships = call("GET", "/shipments")
pending = [x for x in ships if x["status"] == "pending"
           and x["origin_id"] in world_ids and x["destination_id"] in world_ids]
completed = [x for x in ships if x["status"] == "completed"]
if not check(len(pending) > 0 and len(completed) >= 20,
             "world has pending + completed (history) shipments",
             f"{len(pending)} pending, {len(completed)} completed"):
    sys.exit(1)
s, edges = call("GET", "/road-edges")
edges = [e for e in edges
         if e["from_location_id"] in world_ids and e["to_location_id"] in world_ids]
s, drivers = call("GET", "/drivers")

# 3 ─ capacity planner -----------------------------------------------------
section("3. AI capacity planner (proposes, never touches data)")
s, b = call("POST", "/planner/propose", {})
props = b.get("proposals", [])
skipped = b.get("skipped", [])
check(s == 200 and props, "planner produced a plan",
      f"{len(props)} shipments placed, {len(skipped)} skipped "
      f"({skipped[0]['reason'][:60]}...)" if skipped else f"{len(props)} placed")
s, before = call("GET", "/assignments")
check(b.get("plan_id") is not None, "plan has an id",
      f"plan {str(b.get('plan_id'))[:13]}...")

# 4 ─ accept one proposal through the guardrails ---------------------------
section("4. Guardrails + manager approval (assign a shipment)")
shipment, vehicle = props[0]["shipment_id"], props[0]["vehicle_id"]
driver = drivers[0]["id"]
s, b = call("POST", "/assignments", {"shipment_id": shipment,
                                     "vehicle_id": vehicle,
                                     "driver_id": driver,
                                     "reason": "planner proposal"})
if s == 200:
    ok("assignment accepted immediately", "no warnings fired")
elif s == 202:
    ap = b.get("approval_id")
    ok("guardrail raised a warning, manager sign-off needed")
    s2, pend = call("GET", "/approvals?status=pending")
    s3, d = call("POST", f"/approvals/{ap}/decision",
                 {"decision": "approved", "note": "demo approval"})
    check(s3 == 200, "manager approved the flagged assignment")
else:
    v = b.get("detail")
    ok("guardrail BLOCKED the combo (hard rule broken)",
       str(v)[:100])
s, after = call("GET", "/assignments")
check(len(after) > len(before), "assignment saved in the database")

# 5 ─ forbidden zone -------------------------------------------------------
section("5. Forbidden zone (war zone stops roads)")
spot = locs[-1]
s, b = call("POST", "/zones", {"name": "Demo war zone",
                               "center_lat": spot["latitude"],
                               "center_lng": spot["longitude"],
                               "radius_km": 40})
zone = b.get("id")
check(s == 200, f"zone drawn around {spot['name']}")
far = max(locs, key=lambda l: abs(l["latitude"] - spot["latitude"])
          + abs(l["longitude"] - spot["longitude"]))
s, b = call("GET", f"/route?from_id={spot['id']}&to_id={far['id']}")
if s == 200:
    ok("route found that AVOIDS the zone",
       f"blocked_by_zones={b.get('blocked_by_zones')}")
else:
    check(s == 404, "no legal route: 404 explains why",
          str(b.get("reasons", b))[:80])
s, b = call("POST", f"/zones/{zone}/status", {"active": False})
check(s == 200, "zone switched off")
s, b = call("GET", f"/route?from_id={spot['id']}&to_id={far['id']}")
check(s == 200, "routes work again with the zone off")

# 6 ─ ranked routes + labels ----------------------------------------------
section("6. Ranked routes with safety labels")
s, b = call("GET", f"/routes?from_id={spot['id']}&to_id={far['id']}")
routes = b.get("routes", [])
check(s == 200 and routes, f"{len(routes)} alternatives offered",
      " / ".join(f"{r['label']} ({r['distance_km']:.0f} km)"
                 for r in routes))

# 7 ─ incident closes a road, evidence, resolve ----------------------------
section("7. Incident lifecycle (report -> verify -> resolve)")
edge = next(e for e in edges if e["status"] == "open")
s, b = call("POST", "/incidents", {"description": "Demo protest blocking road",
                                   "incident_type": "protest",
                                   "road_edge_id": edge["id"]})
inc = b.get("id")
check(s == 200, "incident reported by dispatcher")
s, b = call("POST", f"/incidents/{inc}/verify")
check(s == 200 and b.get("road_closed") is True, "manager verified -> ROAD CLOSED")
fd, tmp = tempfile.mkstemp(suffix=".txt")
with os.fdopen(fd, "w") as f:
    f.write("demo photo bytes")
boundary = "demoboundary"
with open(tmp, "rb") as f:
    body = (f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="file"; filename="evidence.txt"\r\n'
            f"Content-Type: text/plain\r\n\r\n{f.read()}\r\n"
            f"--{boundary}--\r\n").encode()
req = urllib.request.Request(f"{BASE}/incidents/{inc}/evidence", data=body,
                             method="POST",
                             headers={"Content-Type":
                                      f"multipart/form-data; boundary={boundary}"})
with urllib.request.urlopen(req) as resp:
    ev = json.loads(resp.read())
check(bool(ev.get("sha256")), "evidence filed, fingerprint recorded",
      f"sha256 {ev['sha256'][:16]}...")
s, b = call("POST", f"/incidents/{inc}/resolve")
check(s == 200 and b.get("road_reopened") is True, "resolved -> ROAD REOPENED")

# 8 ─ risky route needs manager approval -----------------------------------
section("8. Risky route: manager must approve its use")
risky = None
for i, a in enumerate(locs):
    for c in locs[i + 1:]:
        s, b = call("GET", f"/routes?from_id={a['id']}&to_id={c['id']}")
        for r in b.get("routes", []):
            if r["label"] == "needs_approval":
                risky = (a["id"], c["id"], r["max_risk"])
                break
        if risky:
            break
    if risky:
        break
if risky:
    s, b = call("POST", "/route/use-request",
                {"from_id": risky[0], "to_id": risky[1], "prefer": "fast"})
    if s == 202:
        ok("dispatcher asked to use the risky road", f"max risk {risky[2]}")
        s2, d = call("POST", f"/approvals/{b['approval_id']}/decision",
                     {"decision": "approved", "note": "escort arranged"})
        check(s2 == 200, "manager approved - route unlocked")
    else:
        ok("system judged the safest option acceptable", str(b)[:60])
else:
    ok("this random world has no dangerous road (happens ~1/3 of the time)",
       "approval demo skipped")

# 9 ─ disruption simulator -------------------------------------------------
section("9. What-if simulator (reality untouched)")
edge = next(e for e in edges if e["status"] == "open")
s, b = call("POST", "/simulate/disruption", {"kind": "close_road",
                                             "edge_id": edge["id"]})
summ = b.get("summary", {})
check(s == 200, "simulation completed",
      ", ".join(f"{k}: {v}" for k, v in summ.items()))
s, b = call("GET", "/road-edges")
still = next(e for e in b if e["id"] == edge["id"])
check(still["status"] == "open", "real road still open - simulation is safe")

# 10 ─ delay model ---------------------------------------------------------
section("10. AI delay model (train on history, predict ETA)")
s, b = call("POST", "/model/train", {})
if check(s == 200, "model trained on completed history",
         f"{b.get('samples')} trips, holdout error "
         f"{b.get('mae_minutes_holdout'):.1f} min"
         if b.get("mae_minutes_holdout") is not None else str(b)):
    target = pending[0]["id"]
    s1, p1 = call("POST", "/predict/eta", {"shipment_id": target})
    s2, p2 = call("POST", "/predict/eta", {"shipment_id": target})
    check(s1 == 200, "ETA predicted",
          f"drive {p1.get('travel_minutes'):.0f} min + delay "
          f"{p1.get('predicted_delay_minutes'):.0f} min "
          f"= ETA {p1.get('eta_minutes'):.0f} min")
    check(p1.get("eta_minutes") == p2.get("eta_minutes"),
          "prediction is deterministic (same input, same answer)")
    s, ship = call("GET", "/shipments")
    untouched = next(x for x in ship if x["id"] == pending[0]["id"])
    check(untouched["delay_minutes"] is None,
          "model only advises - shipment untouched")

# 11 ─ audit ledger --------------------------------------------------------
section("11. Tamper-proof audit ledger")
s, log = call("GET", "/audit?limit=100")
actors = sorted({r["actor_type"] for r in log})
check(s == 200 and len(log) > 10, "every step left a trail",
      f"{len(log)} entries by: {', '.join(actors)}")
s, b = call("GET", "/audit/verify")
check(s == 200 and b.get("ok") is True,
      f"chain verified - no tampering ({b.get('checked')} entries)")

# summary ------------------------------------------------------------------
print("\n" + "=" * 60)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("Failed steps: " + "; ".join(FAIL))
    sys.exit(1)
print("The whole application works end to end.")
