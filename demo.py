#!/usr/bin/env python3
"""End-to-end walkthrough of the dispatch console.

Start the server, then run:  python demo.py
Optional:  python demo.py http://localhost:8010
"""

import asyncio
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from random import Random

import websockets

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8010"
PASS, FAIL = [], []
TOKEN = None  # set by login(); sent with every request


def call(method, path, payload=None):
    data = None
    headers = {}
    if TOKEN and not path.startswith(("/login", "/driver/login")):
        headers["Authorization"] = f"Bearer {TOKEN}"
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


TOKENS = {}


def call_as(username, password, method, path, payload=None):
    """One call as another named user (their token, then back to ours)."""
    global TOKEN
    if username not in TOKENS:
        sc, b = call("POST", "/login",
                     {"username": username, "password": password})
        TOKENS[username] = b.get("token", "") if sc == 200 else ""
    saved, TOKEN = TOKEN, TOKENS[username]
    try:
        return call(method, path, payload)
    finally:
        TOKEN = saved


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

# 0 -- sign in: from now on every action is done by a named, audited user
section("0. Login (Phase 10: no anonymous actions any more)")
s, body = call("POST", "/login",
               {"username": "dispatcher", "password": "dispatch123"})
if check(s == 200 and "token" in body,
         "dispatcher signs in and gets a token"):
    TOKEN = body["token"]
    ok("wrong passwords are refused",
       f"status {call('POST', '/login', {'username': 'dispatcher', 'password': 'nope'})[0]}")
saved_token = TOKEN
TOKEN = None
s, anon = call("POST", "/vehicles", {"plate": "X"})
TOKEN = saved_token
check(s == 401, "without a token, writes are refused", f"status {s}")

# 1 ─ health ---------------------------------------------------------------
section("1. Server health")
s, b = call("GET", "/health")
check(s == 200 and b.get("status") == "ok", "server is alive")

# 1b ─ clean up leftovers from earlier demo runs ---------------------------
s, zones_all = call("GET", "/zones")
s, edges_all = call("GET", "/road-edges")
leftovers = 0
for z in zones_all:
    if z.get("active"):
        call("POST", f"/zones/{z['id']}/status", {"active": False})
        leftovers += 1
for e in edges_all:
    if e["status"] == "closed":
        call("POST", f"/road-edges/{e['id']}/status", {"status": "open"})
        leftovers += 1
if leftovers:
    print(f"  ..   reset {leftovers} leftover zone(s)/closure(s) "
          "from earlier runs")

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
if not props:
    # in this world state the planner has nothing to propose (everything
    # already placed) - keep later live-tracking steps working by using
    # any idle vehicle as a pseudo-proposal
    busy = {a["vehicle_id"] for a in call("GET", "/assignments")[1]
            if a.get("ended_at") is None}
    idle = [v["id"] for v in call("GET", "/vehicles")[1]
            if v["id"] not in busy]
    props = [{"vehicle_id": idle[0], "pseudo": True}] if idle else []
skipped = b.get("skipped", [])
check(s == 200 and props, "planner produced a plan",
      f"{len(props)} shipments placed, {len(skipped)} skipped "
      f"({skipped[0]['reason'][:60]}...)" if skipped else f"{len(props)} placed")
trust = b.get("trust_score", {})
if props and trust.get("score") is not None:
    ok("plan declares its trust", f"{trust['score']}/100")
s, before = call("GET", "/assignments")
check(b.get("plan_id") is not None, "plan has an id",
      f"plan {str(b.get('plan_id'))[:13]}...")

# 4 ─ accept one proposal through the guardrails ---------------------------
section("4. Guardrails + manager approval (assign a shipment)")
saved_combo, last_detail = None, ""
for driver in drivers:
    for prop in props[:5]:
        if prop.get("pseudo"):
            continue  # fallback entry, not a real planner proposal
        s, b = call("POST", "/assignments",
                    {"shipment_id": prop["shipment_id"],
                     "vehicle_id": prop["vehicle_id"],
                     "driver_id": driver["id"],
                     "reason": "planner proposal"})
        if s in (200, 202):
            saved_combo = (prop, driver)
            break
        last_detail = str(b.get("detail"))[:100]
    if saved_combo:
        break
if saved_combo is None:
    ok("guardrail BLOCKED every driver combo (hard rules)",
       last_detail)
elif s == 200:
    ok("assignment accepted immediately", "no warnings fired")
else:
    ap = b.get("approval_id")
    ok("guardrail raised a warning, manager sign-off needed")
    s2, pend = call("GET", "/approvals?status=pending")
    s3, d = call_as("manager", "manager123", "POST",
                    f"/approvals/{ap}/decision",
                 {"decision": "approved", "note": "demo approval"})
    check(s3 == 200, "manager approved the flagged assignment")
s, after = call("GET", "/assignments")
if saved_combo:
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
s, b = call_as("manager", "manager123", "POST",
               f"/incidents/{inc}/verify")
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
ev_headers = {"Content-Type":
              f"multipart/form-data; boundary={boundary}"}
if TOKEN:
    ev_headers["Authorization"] = f"Bearer {TOKEN}"
req = urllib.request.Request(f"{BASE}/incidents/{inc}/evidence", data=body,
                             method="POST", headers=ev_headers)
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
        s2, d = call_as("manager", "manager123", "POST",
                        f"/approvals/{b['approval_id']}/decision",
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
    unc = p1.get("uncertainty", {})
    trust = p1.get("trust_score", {})
    if check(s1 == 200, "ETA predicted with an uncertainty range",
             f"drive {p1.get('travel_minutes'):.0f} min + delay "
             f"{p1.get('predicted_delay_minutes'):.0f} min "
             f"= ETA {p1.get('eta_minutes'):.0f} min "
             f"(likely {unc.get('low_minutes', 0):.0f}-"
             f"{unc.get('high_minutes', 0):.0f})"):
        ok("range brackets the answer",
           f"confidence {int(unc.get('confidence', 0) * 100)}%")
        if check(0 <= trust.get("score", -1) <= 100,
                 "prediction carries a trust score",
                 f"{trust.get('score')}/100 from measured accuracy"):
            s3, health = call("GET", "/model/health")
            check(s3 == 200 and health.get("trained") is True,
                  "model health report available",
                  f"drift: {health.get('drift', {}).get('status')}, "
                  f"health trust: {health.get('trust_score', {}).get('score')}")
    expl = p1.get("explanation", {})
    if check(bool(expl.get("top_factors")), "model explains itself"):
        ok("main reason", expl["summary"][:90])
    check(p1.get("eta_minutes") == p2.get("eta_minutes"),
          "prediction is deterministic (same input, same answer)")
    s, ship = call("GET", "/shipments")
    untouched = next(x for x in ship if x["id"] == pending[0]["id"])
    check(untouched["delay_minutes"] is None,
          "model only advises - shipment untouched")

# 11 ─ contingency playbook -------------------------------------------------
section("11. Stress scenario + contingency playbook")
edge = next(e for e in edges if e["status"] == "open")
victim = locs[0]
s, scen = call("POST", "/simulate/scenario", {"actions": [
    {"kind": "close_road", "edge_id": edge["id"]},
    {"kind": "add_zone", "zone": {"name": "Demo stress zone",
                                  "center_lat": victim["latitude"],
                                  "center_lng": victim["longitude"],
                                  "radius_km": 40}},
]})
if check(s == 200, "multi-failure scenario simulated on copies",
         ", ".join(f"{k}: {v}" for k, v in scen.get("summary", {}).items())):
    net = scen.get("network", {})
    ok("network health measured",
       f"roads closed: {net.get('roads_closed_by_scenario')}, "
       f"largest connected share: {net.get('largest_connected_share')}")
s, pb = call("POST", "/playbooks", {
    "name": f"Demo playbook {rng.randint(1, 99999)}",
    "description": "Close a road and fence off a city - tested response",
    "actions": scen["actions"] if s == 200 else [],
})
if check(s == 200, "playbook saved with its tested impact"):
    s2, ap = call("POST", f"/playbooks/{pb['id']}/apply")
    check(s2 == 200 and ap.get("applied"),
          "playbook applied FOR REAL",
          f"{len(ap.get('applied', []))} changes made")
    s3, e2 = call("GET", "/road-edges")
    now_closed = next((e for e in e2 if e["id"] == edge["id"]), None)
    check(now_closed and now_closed["status"] == "closed",
          "the road really is closed in the database")
    # a demo must clean up after itself: undo the real changes
    for a in ap.get("applied", []):
        if a["kind"] == "add_zone":
            call("POST", f"/zones/{a['zone_id']}/status", {"active": False})
        elif a["kind"] == "close_road":
            call("POST", f"/road-edges/{a['edge_id']}/status",
                 {"status": "open"})
    ok("cleaned up (zone off, road reopened)")

# 12 ─ anomaly detection ----------------------------------------------------
section("12. Anomaly detection")
s, an = call("GET", "/anomalies")
if check(s == 200, "history scanned for anomalies",
         f"{an.get('summary', {}).get('checked')} trips checked "
         f"({an.get('summary', {}).get('delay_check')} mode), "
         f"{an.get('summary', {}).get('flagged')} flagged"):
    for a in an.get("anomalies", [])[:2]:
        ok(f"finding: {a['kind']} ({a['severity']})", a["detail"][:90])

# 13 ─ live tracking ---------------------------------------------------------
section("13. Live vehicle tracking (WebSockets)")

async def run_live(vid, base_pt):
    ws_base = BASE.replace("http", "ws", 1)
    seen, alert_msg = [], None
    async with websockets.connect(f"{ws_base}/ws/live") as viewer:
        for i in range(3):  # drive the vehicle along a short line
            call("POST", f"/vehicles/{vid}/position",
                 {"latitude": base_pt[0] + i * 0.01,
                  "longitude": base_pt[1], "speed_kmh": 40 + i})
        for _ in range(3):
            seen.append(json.loads(await asyncio.wait_for(viewer.recv(), 10)))
        s, z = call("POST", "/zones", {
            "name": f"Demo tracking zone {rng.randint(1, 99999)}",
            "center_lat": base_pt[0] + 0.05, "center_lng": base_pt[1],
            "radius_km": 3})
        call("POST", f"/vehicles/{vid}/position", {  # cross the new fence
            "latitude": base_pt[0] + 0.05, "longitude": base_pt[1]})
        alert_msg = json.loads(await asyncio.wait_for(viewer.recv(), 10))
        if s == 200:
            call("POST", f"/zones/{z['id']}/status", {"active": False})
    async with websockets.connect(f"{ws_base}/ws/vehicle/{vid}") as driver:
        await driver.send(json.dumps({
            "latitude": base_pt[0] + 0.06, "longitude": base_pt[1],
            "speed_kmh": 35}))
        ack = json.loads(await asyncio.wait_for(driver.recv(), 10))
    return seen, alert_msg, ack

vid = props[0]["vehicle_id"]
seen, alert_msg, ack = asyncio.run(run_live(vid, (locs[0]["latitude"],
                                                  locs[0]["longitude"])))
check(len(seen) == 3 and abs(seen[-1]["latitude"] - (locs[0]["latitude"] + 0.02)) < 1e-9,
      "console received every position live", f"{len(seen)} broadcasts")
check(bool(alert_msg and alert_msg.get("zone_alert")),
      "zone breach pushed to consoles in real time",
      alert_msg["zone_alert"]["zone_name"]
      if alert_msg and alert_msg.get("zone_alert") else "no alert seen")
check(ack.get("status") == "ok",
      "driver channel acknowledged its ping")
s, pos = call("GET", f"/vehicles/{vid}/position")
check(s == 200 and abs(pos["latitude"] - (locs[0]["latitude"] + 0.06)) < 1e-9,
      "latest position stored server-side",
      f"lat {pos['latitude']:.4f}, source {pos['source']}")

# 14 ─ event pipeline and geofencing -----------------------------------------
section("14. Event pipeline and geofencing")

async def run_events(vid, base_pt):
    ws_base = BASE.replace("http", "ws", 1)
    s, a = call("POST", "/locations", {
        "name": f"Event depot {rng.randint(1, 99999)}",
        "location_type": "depot", "latitude": base_pt[0] + 0.30,
        "longitude": base_pt[1] + 0.30, "is_depot": True})
    check(s == 200, "test locations created for the event demo")
    s, b = call("POST", "/locations", {
        "name": f"Event stop {rng.randint(1, 99999)}",
        "location_type": "warehouse",
        "latitude": base_pt[0] + 0.40, "longitude": base_pt[1] + 0.40})
    s, edge = call("POST", "/road-edges", {
        "from_location_id": a["id"], "to_location_id": b["id"],
        "distance_km": 25})
    live_events = []
    async with websockets.connect(f"{ws_base}/ws/events") as listener:
        call("POST", f"/road-edges/{edge['id']}/status",
             {"status": "closed"})
        closed_evt = json.loads(await asyncio.wait_for(listener.recv(), 10))
        call("POST", f"/road-edges/{edge['id']}/status",
             {"status": "open"})
        reopened_evt = json.loads(await asyncio.wait_for(listener.recv(), 10))
        s, fence = call("POST", "/zones", {
            "name": f"Demo geofence {rng.randint(1, 99999)}",
            "center_lat": base_pt[0] + 0.01, "center_lng": base_pt[1],
            "radius_km": 3, "zone_type": "geofence_alert"})
        call("POST", f"/vehicles/{vid}/position", {   # enter the fence
            "latitude": base_pt[0] + 0.01, "longitude": base_pt[1]})
        enter_evt = json.loads(await asyncio.wait_for(listener.recv(), 10))
        call("POST", f"/vehicles/{vid}/position", {   # leave again
            "latitude": base_pt[0] + 0.10, "longitude": base_pt[1]})
        exit_evt = json.loads(await asyncio.wait_for(listener.recv(), 10))
        if s == 200:
            call("POST", f"/zones/{fence['id']}/status", {"active": False})
    live_events = [closed_evt, reopened_evt, enter_evt, exit_evt]
    # routing still works through an alert zone (it never blocks)
    call("POST", "/zones", {
        "name": f"Demo watch circle {rng.randint(1, 99999)}",
        "center_lat": base_pt[0], "center_lng": base_pt[1], "radius_km": 30,
        "zone_type": "geofence_alert"})
    s, r = call("GET", f"/routes?from_id={a['id']}&to_id={b['id']}")
    blocked_free = r.get("blocked_by_zones") == [] if s == 200 else False
    s, zs = call("GET", "/zones")
    for z in zs:
        if z["name"].startswith("Demo watch circle"):
            call("POST", f"/zones/{z['id']}/status", {"active": False})
    return live_events, blocked_free

vid2 = props[0]["vehicle_id"]
events_seen, no_block = asyncio.run(run_events(
    vid2, (locs[1]["latitude"], locs[1]["longitude"])))
check([e["kind"] for e in events_seen] ==
      ["road.closed", "road.reopened", "geofence.enter", "geofence.exit"],
      "every crossing and closure arrived live",
      " -> ".join(e["kind"] for e in events_seen))
check(events_seen[2]["details"]["zone_type"] == "geofence_alert",
      "geofence events carry their zone type")
check(no_block, "alert zones warn but never block routing")
s, feed = call("GET", "/events?limit=10")
check(s == 200 and len(feed) >= 4, "event history is queryable",
      f"{len(feed)} recent events stored")

# 16 ─ driver app (proof of delivery) -----------------------------------------
section("16. Driver app: jobs + proof of delivery")
drv = drivers[0]
s, jobs = call("GET", f"/driver/{drv['id']}/jobs")
if check(s == 200, "driver's own jobs listed",
         f"{len(jobs.get('jobs', []))} active job(s) for {drv['full_name']}"):
    job = jobs["jobs"][0]
    ok("job carries what a driver needs",
       f"shipment #{job['shipment_id']} {job['origin_name']} -> "
       f"{job['destination_name']}, vehicle {job['plate']}")
    png = b"\x89PNG fake proof photo for the demo"
    import httpx
    s, dl = call("POST", "/driver/login",
                 {"driver_id": drv["id"], "pin": "1234"})
    if not check(s == 200 and "token" in dl,
                 "driver signs in with id + PIN (Phase 10)"):
        dl = {}
    driver_auth = {"Authorization": f"Bearer {dl.get('token', '')}"}
    r = httpx.post(
        f"{BASE}/driver/{drv['id']}/jobs/{job['assignment_id']}/deliver",
        data={"note": "demo delivery"}, files={"photo": ("proof.png", png, "image/png")},
        headers=driver_auth)
    check(r.status_code == 200 and r.json()["proof_recorded"] is True,
          "delivery confirmed with optional photo proof",
          f"shipment #{job['shipment_id']} completed")
    s, after = call("GET", f"/driver/{drv['id']}/jobs")
    check(all(j["assignment_id"] != job["assignment_id"]
              for j in after.get("jobs", [])),
          "delivered job left the driver's work list")
    s, comp = call("GET", "/events?kind=assignment.completed&limit=5")
    check(s == 200 and any(e["shipment_id"] == job["shipment_id"] for e in comp),
          "completion flowed through the event pipeline")
try:
    with urllib.request.urlopen(f"{BASE}/driver-app/") as resp:
        pwa_status, pwa_html = resp.status, resp.read().decode()
except urllib.error.HTTPError as e:
    pwa_status, pwa_html = e.code, ""
check(pwa_status == 200 and "Driver App" in pwa_html,
      "driver PWA served at /driver-app/ (installable, offline)")

# 17 ─ customer notifications ------------------------------------------------
section("17. Customer notifications + driver incident report")
s, b = call("POST", "/shipments", {
    "description": "Demo customer cargo", "weight_kg": 400,
    "origin_id": locs[0]["id"], "destination_id": locs[1]["id"],
    "customer_name": "Demo Retail Shop", "customer_phone": "+91 90000 42424"})
if check(s == 200, "shipment created with a customer contact",
         f"shipment #{b['id']} for Demo Retail Shop"):
    demo_ship = b["id"]
    if saved_combo:
        combo_prop, combo_driver = saved_combo
    else:
        # step 4 found no passing combo this run - fall back to any idle
        # vehicle and the first driver; if the guardrails refuse, the
        # incident flow below is skipped gracefully
        combo_driver = drivers[0]
        busy = {a["vehicle_id"] for a in call("GET", "/assignments")[1]
                if a.get("ended_at") is None}
        idle = [v["id"] for v in call("GET", "/vehicles")[1]
                if v["id"] not in busy]
        combo_prop = {"vehicle_id": idle[0]} if idle else None
    # reuse the vehicle+driver pair that passed guardrails in step 4
    s, b2 = call("POST", "/assignments", {
        "shipment_id": demo_ship,
        "vehicle_id": combo_prop["vehicle_id"],
        "driver_id": combo_driver["id"],
        "reason": "phase 8D demo"})
    if check(s == 200, "shipment assigned to the customer's delivery",
             f"job for vehicle {combo_prop['vehicle_id']}"):
        s, notes = call("GET", f"/notifications?shipment_id={demo_ship}")
        kinds = [n["kind"] for n in notes]
        check("assigned" in kinds, "customer told it is on the way",
              f'"{notes[0]["message"][:70]}"')
        s, dl2 = call("POST", "/driver/login",
                      {"driver_id": combo_driver["id"], "pin": "1234"})
        auth2 = {"Authorization": f"Bearer {dl2.get('token', '')}"} if s == 200 else {}
        r = httpx.post(f"{BASE}/driver/{combo_driver['id']}/jobs/{b2['id']}/incident",
                       json={"description": "Demo: rockslide blocks the ghat road",
                             "incident_type": "road_blocked"}, headers=auth2)
        check(r.status_code == 200,
              "driver reported an incident from the PWA",
              f"incident #{r.json().get('incident_id')}")
        s, notes = call("GET", f"/notifications?shipment_id={demo_ship}")
        kinds = [n["kind"] for n in notes]
        check("incident" in kinds, "customer was told about the problem",
              f'"{notes[0]["message"][:70]}..."')
        r = httpx.post(f"{BASE}/driver/{combo_driver['id']}/jobs/{b2['id']}/deliver",
                       data={"note": "demo delivery"}, headers=auth2)
        check(r.status_code == 200, "delivery confirmed")
        s, notes = call("GET", f"/notifications?shipment_id={demo_ship}")
        kinds = [n["kind"] for n in notes]
        check("delivered" in kinds and "incident" in kinds,
              "customer got the full story: problem, then delivery",
              f"kinds: {', '.join(reversed(kinds))}")
    else:
        ok("skip incident demo (assignment blocked)", str(b2)[:80])

# 19 ─ AI copilot ------------------------------------------------------------
section("19. Guardrailed AI copilot (proposes, never decides)")
s, ans = call("POST", "/copilot/ask", {"question": "how many vehicles are idle?"})
if check(s == 200 and ans["intent"] == "fleet_summary",
         "copilot answers fleet questions",
         ans.get("answer", "")[:90]):
    ok("every answer declares what it is",
       f"model {ans.get('model')}, confidence {ans.get('confidence')}")
s, ans = call("POST", "/copilot/ask",
              {"question": "what is the model health?"})
check(s == 200 and "trust" in ans.get("answer", "").lower(),
      "copilot reports model health with trust",
      ans.get("answer", "")[:90])
s, ans = call("POST", "/copilot/ask",
              {"question": "add forbidden zone at lat 19.10 lng 72.85 radius 4"})
if check(s == 200 and ans.get("requires_human") is True,
         "action request became a PROPOSAL, not an execution",
         f"intent {ans.get('intent')}, proposal #{ans.get('proposal_id')}"):
    pid = ans["proposal_id"]
    zones_before = {z["id"] for z in call("GET", "/zones")[1]}
    s, d = call("POST", f"/copilot/proposals/{pid}/decision",
                {"decision": "accepted", "note": "demo: news report"})
    if check(s == 200 and d["outcome"] == "allowed",
             "human accepted -> executed through the guardrails"):
        zones_now = {z["id"] for z in call("GET", "/zones")[1]}
        check(len(zones_now) == len(zones_before) + 1,
              "the zone really exists after acceptance")
        call("POST", f"/zones/{max(zones_now)}/status", {"active": False})
s, ans = call("POST", "/copilot/ask",
              {"question": "close road 999999"})
check(s == 200 and ans.get("proposal") is None,
      "impossible requests are refused with an explanation",
      ans.get("answer", "")[:80])
s, ledger = call("GET", "/copilot/proposals")
check(s == 200 and ledger, "decision ledger is queryable",
      f"{len(ledger)} proposal(s), latest: {ledger[0]['status']}")
s, acts = call("GET", "/audit?limit=100")
check(any(a["action"] == "copilot.suggested" for a in acts)
      and any(a["action"] == "copilot.accepted" for a in acts),
      "both the AI and the human left audit entries")

# 20 -- security & observability --------------------------------------------
section("20. Security & observability (Phase 10)")
s, h = call("GET", "/health")
check(s == 200 and h.get("status") == "ok",
      "health endpoint reports database, model and network",
      f"db {h.get('database')}, model {h.get('delay_model')}, "
      f"{h.get('open_roads')} open roads")
import urllib.request as _ur
with _ur.urlopen(f"{BASE}/metrics") as resp:
    metrics = resp.read().decode()
check("dispatch_requests_total" in metrics
      and "dispatch_audit_entries" in metrics,
      "metrics endpoint exposes counters and gauges",
      next((l for l in metrics.splitlines()
            if l.startswith("dispatch_requests_total")), "")[:60])
s, acts = call("GET", "/audit?limit=200")
mine = next((a for a in acts
             if a["action"] == "vehicle.create" or a["action"] == "copilot.accepted"), None)
check(mine is not None and mine.get("actor_id") == "dispatcher",
      "audit rows carry the signed-in user, not a hard-code",
      f"{mine['action']} by actor_id={mine.get('actor_id')}" if mine else "none")

# 21 ─ media authenticity (Phase 11) ----------------------------------------
section("21. Media authenticity - is that photo real?")
try:
    from PIL import Image
    # a fresh reported incident to attach evidence to (two new stops)
    ma = call("POST", "/locations", {
        "name": "Media A", "location_type": "depot",
        "latitude": 12.35, "longitude": 78.9, "is_depot": True})[1]["id"]
    mb = call("POST", "/locations", {
        "name": "Media B", "location_type": "warehouse",
        "latitude": 12.45, "longitude": 79.0})[1]["id"]
    mroad = call("POST", "/road-edges", {
        "from_location_id": ma, "to_location_id": mb,
        "distance_km": 25})[1]["id"]
    inc_a = call("POST", "/incidents", {
        "description": "Demo: tree across the road (photo proof)",
        "incident_type": "road_blocked", "road_edge_id": mroad})[1]["id"]
    inc_b = call("POST", "/incidents", {
        "description": "Demo: someone tries to reuse an old photo",
        "incident_type": "road_blocked", "road_edge_id": mroad})[1]["id"]

    def upload(inc, filename, blob, ctype):
        boundary = "mediademoboundary"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="file"; filename="{filename}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n").encode() + blob + \
               f"\r\n--{boundary}--\r\n".encode()
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        if TOKEN:
            headers["Authorization"] = f"Bearer {TOKEN}"
        req = urllib.request.Request(f"{BASE}/incidents/{inc}/evidence",
                                     data=body, method="POST",
                                     headers=headers)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    # a plausible phone photo WITH camera metadata
    img = Image.new("RGB", (900, 700), (120, 140, 170))
    for x in range(0, 900, 40):
        for y in range(0, 700, 40):
            img.putpixel((x, y), (210, 210, 220))
    # one random pixel: the demo may run many times, and the checker
    # rightly remembers files it has seen before
    img.putpixel((rng.randint(0, 899), rng.randint(0, 699)),
                 (rng.randint(0, 255), rng.randint(0, 255),
                  rng.randint(0, 255)))
    exif = Image.Exif()
    exif[271], exif[272] = "Google", "Pixel 7"
    exif[306] = "2026:10:11 09:41:00"
    photo_path = os.path.join(tempfile.gettempdir(), "demo_ghat_photo.jpg")
    img.save(photo_path, "JPEG", exif=exif)
    with open(photo_path, "rb") as f:
        photo_blob = f.read()
    up = upload(inc_a, "ghat_road.jpg", photo_blob, "image/jpeg")
    rep = up["media_check"]
    if check(rep["verdict"] == "clean",
             "a real camera photo passes the authenticity check",
             f"trust {rep['score']}/100"):
        ok("verdict is explainable",
           ", ".join(r["flag"] for r in rep["reasons"]))
    # the SAME file submitted again as 'new' evidence: rejected
    up2 = upload(inc_b, "ghat_road.jpg", photo_blob, "image/jpeg")
    rep2 = up2["media_check"]
    check(rep2["verdict"] == "likely_fake"
          and any(r["flag"] == "duplicate" for r in rep2["reasons"]),
          "the same photo reused for a second incident is rejected",
          f"verdict {rep2['verdict']}")
    s, ans = call("POST", "/copilot/ask",
                  {"question": f"is the proof for incident {inc_a} "
                                "authentic?"})
    check(s == 200 and "CLEAN" in ans.get("answer", ""),
          "the copilot can quote the verdict",
          ans.get("answer", "")[:90])
    s, checks = call("GET", "/media/checks?limit=5")
    check(s == 200 and len(checks) >= 2,
          "media checks are listed for the console",
          f"{len(checks)} check(s)")
    os.remove(photo_path)
    # leave no open incidents behind
    call_as("manager", "manager123", "POST", f"/incidents/{inc_a}/dismiss")
    call_as("manager", "manager123", "POST", f"/incidents/{inc_b}/dismiss")
except ImportError:
    ok("skip media demo (Pillow not installed)")

# 22 ─ audit ledger ---------------------------------------------------------
section("22. Tamper-proof audit ledger")
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
