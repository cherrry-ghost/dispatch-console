"""Guardrailed AI copilot (Phase 9).

A deterministic intent engine: the dispatcher asks in plain text, the
copilot answers from the system's own data and intelligence (planner,
delay model, anomaly scan) and - crucially - turns every ACTION into a
stored proposal. The copilot proposes; only a human decision endpoint can
execute, and execution always goes through the existing guardrailed
endpoints. Nothing here touches data directly.

Every suggestion lands in the Decision Ledger (copilot_proposals) with its
input snapshot, guardrail results and confidence, and stays replayable.
"""
import re
from datetime import date

from guardrails import evaluate, is_allowed
from planner import plan_shipments
from trust import eta_trust
from delay_model import (
    model_exists, predict as predict_delay, load as load_model,
    route_features, travel_minutes,
)
from anomalies import scan as scan_anomalies
from routing import shortest_route

# a vehicle is idle when it has no active (open-ended) assignment
_IDLE_VEHICLES = (
    "SELECT * FROM vehicles v WHERE NOT EXISTS ("
    "SELECT 1 FROM assignments a WHERE a.vehicle_id = v.id "
    "AND a.ended_at IS NULL)")


def _num(text, pattern):
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1) if match else None


def _holdout_metrics(conn):
    row = conn.execute(
        "SELECT details FROM audit_log WHERE action = 'model.trained' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    details = row["details"]
    return {
        "samples": details.get("samples"),
        "mae_minutes": details.get("mae_minutes_holdout"),
        "interval_coverage": details.get("interval_coverage_holdout"),
    }


def _pick_driver(conn, vehicle, shipment):
    """Deterministically find a driver whose guardrails pass for this
    vehicle + shipment. Returns (driver_row_or_None, last_results)."""
    last = []
    for driver in conn.execute("SELECT * FROM drivers ORDER BY id").fetchall():
        results = evaluate(vehicle, driver, shipment, date.today())
        last = results
        if is_allowed(results):
            return driver, results
    return None, last


def _pretty(results):
    return [{"rule": r.rule, "severity": r.severity, "message": r.message}
            for r in results]


# ---------------------------------------------------------------- intents

def intent_fleet(conn, q, ctx):
    total = conn.execute("SELECT count(*) AS n FROM vehicles").fetchone()["n"]
    busy = conn.execute(
        "SELECT count(DISTINCT vehicle_id) AS n FROM assignments "
        "WHERE ended_at IS NULL").fetchone()["n"]
    drivers = conn.execute("SELECT count(*) AS n FROM drivers").fetchone()["n"]
    answer = (f"Fleet: {total} vehicles - {busy} on assignment, "
              f"{total - busy} idle. {drivers} drivers on the roster.")
    return {"intent": "fleet_summary", "answer": answer, "proposal": None,
            "guardrail_results": [], "alternatives": []}


def intent_shipments(conn, q, ctx):
    total = conn.execute("SELECT count(*) AS n FROM shipments").fetchone()["n"]
    pending = conn.execute(
        "SELECT count(*) AS n FROM shipments WHERE status = 'pending'"
    ).fetchone()["n"]
    hazmat = conn.execute(
        "SELECT count(*) AS n FROM shipments WHERE is_hazardous"
    ).fetchone()["n"]
    on_road = conn.execute(
        "SELECT count(*) AS n FROM shipments WHERE status = 'assigned'"
    ).fetchone()["n"]
    answer = (f"Shipments: {total} total - {pending} pending, {on_road} on "
              "the road"
              + (f", {hazmat} hazardous (need a hazmat-certified vehicle)"
                 if hazmat else "") + ".")
    return {"intent": "shipments_summary", "answer": answer,
            "proposal": None, "guardrail_results": [], "alternatives": []}


def intent_plan(conn, q, ctx):
    pending = conn.execute(
        "SELECT * FROM shipments WHERE status = 'pending'").fetchall()
    vehicles = conn.execute(_IDLE_VEHICLES).fetchall()
    if not pending or not vehicles:
        return {"intent": "propose_plan",
                "answer": "Nothing to plan right now: I need both pending "
                          "shipments and idle vehicles.",
                "proposal": None, "guardrail_results": [], "alternatives": []}
    edges = conn.execute("SELECT * FROM road_edges").fetchall()
    locations = conn.execute(
        "SELECT id, latitude, longitude, is_depot FROM locations "
        "ORDER BY id").fetchall()
    plan = plan_shipments(pending, vehicles, date.today(),
                          edges=edges, locations=locations)
    proposals = plan["proposals"]
    first = proposals[0] if proposals else None
    proposal, guardrail_results = None, []
    if first:
        shipment = next((s for s in pending
                         if s["id"] == first["shipment_id"]), None)
        vehicle = next((v for v in vehicles
                        if v["id"] == first["vehicle_id"]), None)
        driver, results = _pick_driver(conn, vehicle, shipment)
        guardrail_results = _pretty(results)
        if driver is not None:
            proposal = {"action": "assign",
                        "shipment_id": first["shipment_id"],
                        "vehicle_id": first["vehicle_id"],
                        "driver_id": driver["id"],
                        "reason": "copilot plan suggestion"}
    answer = (f"I drafted a plan: {plan['summary']['shipments_planned']} of "
              f"{plan['summary']['shipments_considered']} shipments placed, "
              f"{len(plan['skipped'])} skipped. Top suggestion: shipment "
              f"#{first['shipment_id']} on vehicle #{first['vehicle_id']}"
              + (f" with driver #{proposal['driver_id']}. Accept below to "
                 "execute it through the guardrails." if proposal else
                 " - but no driver passed the guardrails, so I will not "
                 "propose it for execution.") + " You decide.")
    alternatives = [{"shipment_id": p["shipment_id"],
                     "vehicle_id": p["vehicle_id"]}
                    for p in proposals[1:4]]
    return {"intent": "propose_plan", "answer": answer, "proposal": proposal,
            "guardrail_results": guardrail_results,
            "alternatives": alternatives}


def intent_predict(conn, q, ctx):
    shipment_id = _num(q, r"shipment\s*#?(\d+)")
    if not shipment_id:
        return _help("Tell me which shipment, e.g. 'predict delay for "
                     "shipment 12'.", intent="predict_delay")
    shipment = conn.execute("SELECT * FROM shipments WHERE id = %s",
                            (int(shipment_id),)).fetchone()
    if shipment is None or shipment["status"] not in ("pending", "assigned"):
        return _help(f"Shipment {shipment_id} is not open for a "
                     "prediction.", intent="predict_delay")
    if not model_exists():
        return _help("No trained delay model yet. Train it with "
                     "POST /model/train first.", intent="predict_delay")
    route = shortest_route(conn.execute(
        "SELECT * FROM road_edges").fetchall(),
        shipment["origin_id"], shipment["destination_id"])
    if route is None:
        return _help("No open route exists for this shipment, so I cannot "
                     "predict a delay.", intent="predict_delay")
    features = route_features(route, shipment)
    low, mid, high = predict_delay(load_model(), features)
    drive = travel_minutes(features)
    holdout = _holdout_metrics(conn) or {}
    trust, factors = eta_trust(
        holdout.get("mae_minutes"), holdout.get("interval_coverage"),
        uncertainty_width=high - low, samples=holdout.get("samples"))
    answer = (f"Shipment #{shipment_id}: expect about {mid:.0f} min extra "
              f"delay (likely {low:.0f}-{high:.0f}). The drive itself is "
              f"about {drive:.0f} min over {features['distance_km']:.0f} km. "
              f"Trust {trust}/100. This is advice, not a decision.")
    return {"intent": "predict_delay", "answer": answer, "proposal": None,
            "guardrail_results": [],
            "alternatives": {"trust": trust, "trust_factors": factors,
                             "uncertainty": {"low_minutes": low,
                                             "high_minutes": high}}}


def intent_model_health(conn, q, ctx):
    health_fn = ctx.get("model_health")
    if health_fn is None:
        return _help("No model report is available right now.",
                 intent="model_health")
    health = health_fn()
    if not health.get("trained"):
        return _help("The delay model has not been trained yet. Run "
                     "POST /model/train (it needs completed history).",
                 intent="model_health")
    holdout = health.get("holdout", {})
    drift = health.get("drift", {})
    answer = (f"Delay model v{health['model_version']}: trained on "
              f"{health.get('samples')} trips, holdout error "
              f"{holdout.get('mae_minutes')} min, honest ranges "
              f"{(holdout.get('interval_coverage') or 0):.0%}. Drift: "
              f"{drift.get('status')}. Overall trust "
              f"{health['trust_score']['score']}/100.")
    return {"intent": "model_health", "answer": answer, "proposal": None,
            "guardrail_results": [], "alternatives": drift}


def intent_anomalies(conn, q, ctx):
    completed = conn.execute(
        "SELECT * FROM shipments WHERE status = 'completed' "
        "ORDER BY id").fetchall()
    edges = conn.execute("SELECT * FROM road_edges").fetchall()
    has_model = model_exists()
    models = load_model() if has_model else None
    predict_fn = (lambda f: predict_delay(models, f)[1]) if has_model else None
    features_by_id = {}
    for s in completed:
        route = shortest_route(edges, s["origin_id"], s["destination_id"])
        features_by_id[s["id"]] = route_features(route, s) if route else None
    findings = scan_anomalies(completed, features_by_id,
                              predict_fn=predict_fn)
    answer = (f"Scanned {len(completed)} completed trips: {len(findings)} "
              "suspicious finding(s)." if findings else
              f"Scanned {len(completed)} completed trips: nothing "
              "suspicious.")
    return {"intent": "anomalies", "answer": answer, "proposal": None,
            "guardrail_results": [], "alternatives": findings[:3]}


def intent_assign(conn, q, ctx):
    shipment_id = _num(q, r"shipment\s*#?(\d+)")
    vehicle_id = _num(q, r"vehicle\s*#?(\d+)")
    if not shipment_id or not vehicle_id:
        return _help("Say it like: 'assign shipment 12 to vehicle 3 and "
                     "driver 2' (the driver is optional - I will pick one "
                     "who passes the guardrails).",
                 intent="assign_shipment")
    shipment = conn.execute("SELECT * FROM shipments WHERE id = %s",
                            (int(shipment_id),)).fetchone()
    vehicle = conn.execute("SELECT * FROM vehicles WHERE id = %s",
                           (int(vehicle_id),)).fetchone()
    if shipment is None or vehicle is None:
        return _help("That shipment or vehicle does not exist.",
                 intent="assign_shipment")
    driver_id = _num(q, r"driver\s*#?(\d+)")
    if driver_id:
        driver = conn.execute("SELECT * FROM drivers WHERE id = %s",
                              (int(driver_id),)).fetchone()
        results = (evaluate(vehicle, driver, shipment, date.today())
                   if driver else [])
        if driver is None:
            return _help("No driver with that id exists.",
                         intent="assign_shipment")
    else:
        driver_count = conn.execute(
            "SELECT count(*) AS n FROM drivers").fetchone()["n"]
        if driver_count == 0:
            return _help("There are no drivers in the system yet.",
                         intent="assign_shipment")
        driver, results = _pick_driver(conn, vehicle, shipment)
        if driver is None:
            # every driver failed the guardrails - show the verdict honestly
            return {"intent": "assign_shipment",
                    "answer": (f"BLOCKED: no driver passed the guardrails "
                               f"for shipment #{shipment['id']} on vehicle "
                               f"#{vehicle['id']}. I will not offer an "
                               "executable proposal."),
                    "proposal": None,
                    "guardrail_results": _pretty(results),
                    "alternatives": []}
    allowed = is_allowed(results)
    answer = (f"Proposal: shipment #{shipment['id']} on vehicle "
              f"#{vehicle['id']} ({vehicle['plate']}) with driver "
              f"#{driver['id']} - "
              + ("guardrails pass. Accept below to execute, or reject."
                 if allowed else
                 "BLOCKED by guardrails, so I will not offer an executable "
                 "proposal."))
    alternatives = [{"vehicle_id": v["id"], "plate": v["plate"]}
                    for v in conn.execute(
                        _IDLE_VEHICLES + " AND v.id != %s LIMIT 3",
                        (vehicle["id"],)).fetchall()]
    proposal = None if not allowed else {
        "action": "assign", "shipment_id": shipment["id"],
        "vehicle_id": vehicle["id"], "driver_id": driver["id"],
        "reason": "copilot suggestion"}
    return {"intent": "assign_shipment", "answer": answer,
            "proposal": proposal, "guardrail_results": _pretty(results),
            "alternatives": alternatives}


def intent_road(conn, q, ctx):
    edge_id = _num(q, r"road\s*#?(\d+)")
    if not edge_id:
        return _help("Say it like: 'close road 42' or 'reopen road 42'.",
                 intent="road_status")
    closing = not re.search(r"reopen|unblock|\bopen\b", q, re.IGNORECASE)
    edge = conn.execute("SELECT * FROM road_edges WHERE id = %s",
                        (int(edge_id),)).fetchone()
    if edge is None:
        return _help(f"Road {edge_id} does not exist.",
                 intent="road_status")
    answer = (f"Proposal: {'CLOSE' if closing else 'REOPEN'} road #{edge_id} "
              f"(between locations {edge['from_location_id']} and "
              f"{edge['to_location_id']}). Accept below to execute.")
    return {"intent": "road_status", "answer": answer,
            "proposal": {"action": "road_status", "edge_id": int(edge_id),
                         "status": "closed" if closing else "open"},
            "guardrail_results": [], "alternatives": []}


def intent_zone(conn, q, ctx):
    lat = _num(q, r"lat(?:itude)?\s*(-?\d+(?:\.\d+)?)")
    lng = _num(q, r"(?:lng|lon)(?:itude)?\s*(-?\d+(?:\.\d+)?)")
    if not lat or not lng:
        return _help("Say it like: 'add forbidden zone at lat 13.05 lng "
                     "80.25 radius 5 km'.", intent="add_zone")
    radius = float(_num(q, r"radius\s*(\d+(?:\.\d+)?)") or 5)
    kind = ("geofence_alert"
            if re.search(r"watch|alert|geofence", q, re.IGNORECASE)
            else "forbidden")
    answer = (f"Proposal: add a {kind} zone at ({lat}, {lng}), radius "
              f"{radius} km - "
              + ("it will only watch and raise events."
                 if kind == "geofence_alert" else
                 "roads through it will disappear from routing.")
              + " Accept below to execute.")
    return {"intent": "add_zone", "answer": answer,
            "proposal": {"action": "zone",
                         "name": f"Copilot zone at {lat},{lng}",
                         "center_lat": float(lat), "center_lng": float(lng),
                         "radius_km": radius, "zone_type": kind},
            "guardrail_results": [], "alternatives": []}


def _help(extra="", intent=None):
    return {"intent": intent or "help", "confidence": 0.3,
            "answer": (extra + " " if extra else "")
            + "I can summarize the fleet or pending shipments, draft a "
            "plan ('propose a plan'), predict delay ('predict delay for "
            "shipment 12'), report model health or anomalies, and PROPOSE "
            "actions ('assign shipment 3 to vehicle 5', 'close road 42', "
            "'add forbidden zone at lat 13.0 lng 80.0 radius 5'). I never "
            "execute - you accept or reject.",
            "proposal": None, "guardrail_results": [], "alternatives": []}


INTENTS = [
    (r"propose.{0,12}plan|draft.{0,12}plan|plan for (today|tomorrow)|"
     r"what.{0,10}plan", intent_plan),
    (r"predict|eta|how late|delay for shipment", intent_predict),
    (r"model health|drift|trust of (the )?model|is the model",
     intent_model_health),
    (r"anomal|suspicious|weird trips", intent_anomalies),
    (r"assign", intent_assign),
    (r"close road|reopen road|unblock road|\bopen road\b", intent_road),
    (r"(add|create).{0,10}zone|forbidden zone|geofence", intent_zone),
    (r"fleet|\bvehicles\b|\bidle\b|\bdrivers\b", intent_fleet),
    (r"shipment|pending|backlog|orders", intent_shipments),
]


def respond(conn, question, ctx=None):
    """Match the question and build the answer. `proposal` (when present)
    is only a suggestion - a human must accept it before anything runs."""
    q = question.lower().strip()
    if not q:
        return _help()
    for pattern, builder in INTENTS:
        if re.search(pattern, q):
            result = builder(conn, q, ctx or {})
            result["confidence"] = 0.85
            return result
    return _help()
