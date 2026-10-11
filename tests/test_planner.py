import os

import psycopg

from audit import verify_chain
from test_assignments import make_vehicle, make_driver, make_shipment, assign


def make_shipment_with(client, weight, required=None):
    origin = client.post(
        "/locations",
        json={"name": "Depot", "location_type": "depot",
              "latitude": 13.0827, "longitude": 80.2707},
    ).json()["id"]
    destination = client.post(
        "/locations",
        json={"name": "Warehouse", "location_type": "warehouse",
              "latitude": 13.15, "longitude": 80.35},
    ).json()["id"]
    return client.post(
        "/shipments",
        json={"description": "Rice bags", "weight_kg": weight,
              "origin_id": origin, "destination_id": destination,
              "required_vehicle_type": required},
    ).json()["id"]


def propose(client, ids=None):
    body = {} if ids is None else {"shipment_ids": ids}
    return client.post("/planner/propose", json=body)


def test_plans_shipments_and_audits_the_run(client):
    vehicle = make_vehicle(client)
    s1 = make_shipment(client, weight=2000)
    s2 = make_shipment(client, weight=1500)

    response = propose(client)

    assert response.status_code == 200
    data = response.json()
    assert data["plan_id"]
    assert data["summary"]["shipments_planned"] == 2
    assert data["summary"]["vehicles_used"] == 1
    assert all(p["vehicle_id"] == vehicle for p in data["proposals"])
    assert data["proposals"][0]["vehicle_total_load_kg"] == 3500
    assert data["skipped"] == []

    rows = client.get("/audit?limit=50").json()
    plan_rows = [r for r in rows if r["action"] == "plan.propose"]
    assert len(plan_rows) == 1
    assert plan_rows[0]["actor_type"] == "ai_planner"
    assert plan_rows[0]["details"]["input"]["shipment_ids"] == [s1, s2]
    assert plan_rows[0]["details"]["model"] == "ortools-cp-sat"
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        assert verify_chain(conn)["ok"] is True


def test_planner_never_saves_anything(client):
    make_vehicle(client)
    make_shipment(client, weight=2000)

    response = propose(client)

    assert response.status_code == 200
    assert client.get("/assignments").json() == []
    assert all(s["status"] == "pending"
               for s in client.get("/shipments").json())


def test_respects_required_vehicle_type(client):
    make_vehicle(client)  # a truck
    s = make_shipment_with(client, weight=1000, required="van")

    data = propose(client).json()

    assert data["proposals"] == []
    assert data["summary"]["shipments_planned"] == 0
    assert len(data["skipped"]) == 1
    assert data["skipped"][0]["shipment_id"] == s
    assert "van" in data["skipped"][0]["reason"]


def test_skips_shipment_no_vehicle_is_big_enough(client):
    make_vehicle(client)  # 5000 kg
    s = make_shipment_with(client, weight=9000)

    data = propose(client).json()

    assert data["proposals"] == []
    assert data["skipped"][0]["shipment_id"] == s
    assert "9000 kg" in data["skipped"][0]["reason"]


def test_excludes_vehicle_with_missing_insurance(client):
    client.post(
        "/vehicles",
        json={"plate": "TN02CD5678", "vehicle_type": "truck",
              "capacity_kg": 5000},  # no insurance/fitness dates
    )
    make_shipment(client, weight=1000)

    data = propose(client).json()

    assert data["proposals"] == []
    assert data["summary"]["eligible_vehicles"] == 0
    assert "insurance or fitness" in data["skipped"][0]["reason"]


def test_uses_fewest_vehicles(client):
    big = client.post(
        "/vehicles",
        json={"plate": "TN03EF9012", "vehicle_type": "truck",
              "capacity_kg": 2000, "insurance_expiry": "2035-12-31",
              "fitness_expiry": "2035-12-31"},
    ).json()["id"]
    client.post(
        "/vehicles",
        json={"plate": "TN04GH3456", "vehicle_type": "truck",
              "capacity_kg": 1000, "insurance_expiry": "2035-12-31",
              "fitness_expiry": "2035-12-31"},
    )
    make_shipment(client, weight=800)
    make_shipment(client, weight=700)

    data = propose(client).json()

    assert data["summary"]["shipments_planned"] == 2
    assert data["summary"]["vehicles_used"] == 1
    assert all(p["vehicle_id"] == big for p in data["proposals"])


def test_warning_when_plan_loads_vehicle_near_capacity(client):
    vehicle = make_vehicle(client)  # 5000 kg
    make_shipment(client, weight=2300)
    make_shipment(client, weight=2400)  # total 4700 = 94% -> warn

    data = propose(client).json()

    assert data["summary"]["shipments_planned"] == 2
    assert len(data["vehicles"]) == 1
    assert data["vehicles"][0]["vehicle_id"] == vehicle
    assert data["vehicles"][0]["total_load_kg"] == 4700
    warnings = data["vehicles"][0]["warnings"]
    assert len(warnings) == 1
    assert "90% or more" in warnings[0]


def test_cannot_fit_alongside_others_is_explained(client):
    make_vehicle(client)  # 5000 kg
    make_shipment(client, weight=3000)
    make_shipment(client, weight=3000)

    data = propose(client).json()

    assert data["summary"]["shipments_planned"] == 1
    assert len(data["skipped"]) == 1
    assert "alongside" in data["skipped"][0]["reason"]


def test_only_pending_shipments_are_planned(client):
    vehicle = make_vehicle(client)
    driver = make_driver(client, "L-PLANNER-1")
    assigned = make_shipment(client, weight=1000)
    pending = make_shipment(client, weight=1000)
    assert assign(client, assigned, vehicle, driver, "already rolling")
    # 'assigned' shipments are already on a vehicle and must not be re-planned.

    data = propose(client).json()

    assert data["summary"]["shipments_considered"] == 1
    assert [p["shipment_id"] for p in data["proposals"]] == [pending]


def test_shipment_ids_filter(client):
    make_vehicle(client)
    s1 = make_shipment(client, weight=1000)
    s2 = make_shipment(client, weight=1000)

    data = propose(client, ids=[s1]).json()

    assert data["summary"]["shipments_considered"] == 1
    assert [p["shipment_id"] for p in data["proposals"]] == [s1]
    assert s2 not in [p["shipment_id"] for p in data["proposals"]]


def test_empty_fleet_returns_empty_plan(client):
    s = make_shipment(client, weight=1000)

    data = propose(client).json()

    assert data["proposals"] == []
    assert data["summary"]["shipments_planned"] == 0
    assert data["skipped"][0]["shipment_id"] == s


# ---- Phase 5C: time windows, depots, mixed fleet --------------------

def make_world(client, roads):
    """Locations with a depot, joined by the given (a, b, km, status) roads."""
    depot = client.post("/locations", json={
        "name": "Central Depot", "location_type": "depot",
        "latitude": 11.0, "longitude": 78.0, "is_depot": True}).json()["id"]
    mid = client.post("/locations", json={
        "name": "Midtown", "location_type": "city",
        "latitude": 11.3, "longitude": 78.2}).json()["id"]
    far = client.post("/locations", json={
        "name": "Farville", "location_type": "city",
        "latitude": 11.6, "longitude": 78.4}).json()["id"]
    towns = {"depot": depot, "mid": mid, "far": far}
    for a, b, km, status in roads:
        client.post("/road-edges", json={
            "from_location_id": towns[a], "to_location_id": towns[b],
            "distance_km": km, "status": status})
    return towns


def make_fleet_vehicle(client, plate, speed=None, depot=None, vtype="truck"):
    body = {"plate": plate, "vehicle_type": vtype, "capacity_kg": 5000,
            "insurance_expiry": "2035-12-31", "fitness_expiry": "2035-12-31"}
    if speed:
        body["speed_kmh"] = speed
    if depot:
        body["home_depot_id"] = depot
    return client.post("/vehicles", json=body).json()["id"]


def window_shipment(client, origin, dest, ready=0, deadline=None, weight=1000):
    body = {"description": "Timed goods", "weight_kg": weight,
            "origin_id": origin, "destination_id": dest,
            "ready_minute": ready}
    if deadline is not None:
        body["deadline_minute"] = deadline
    return client.post("/shipments", json=body).json()["id"]


def test_deadline_nobody_can_make_is_skipped(client):
    towns = make_world(client, [("depot", "mid", 100, "open"),
                                ("mid", "far", 100, "open")])
    make_fleet_vehicle(client, "TN51AA0001", speed=40, depot=towns["depot"])
    s = window_shipment(client, towns["depot"], towns["far"], deadline=60)

    data = propose(client).json()

    assert data["proposals"] == []
    assert "time windows" in data["skipped"][0]["reason"]


def test_fast_vehicle_makes_the_deadline_slow_one_does_not(client):
    towns = make_world(client, [("depot", "far", 100, "open")])
    slow = make_fleet_vehicle(client, "TN52AA0002", speed=20,
                              depot=towns["depot"])
    fast = make_fleet_vehicle(client, "TN52BB0003", speed=60,
                              depot=towns["depot"], vtype="van")
    s = window_shipment(client, towns["depot"], towns["far"], deadline=150)

    data = propose(client).json()

    assert [p["vehicle_id"] for p in data["proposals"]] == [fast]
    assert slow != fast


def test_vehicle_waits_for_the_ready_time(client):
    towns = make_world(client, [("depot", "far", 100, "open")])
    make_fleet_vehicle(client, "TN53AA0004", speed=60, depot=towns["depot"])
    s = window_shipment(client, towns["depot"], towns["far"],
                        ready=600, deadline=900)

    data = propose(client).json()

    assert data["summary"]["shipments_planned"] == 1
    p = data["proposals"][0]
    assert p["pickup_minute"] >= 600
    assert p["drop_minute"] <= 900


def test_stops_come_back_in_route_order(client):
    towns = make_world(client, [("depot", "mid", 60, "open"),
                                ("mid", "far", 80, "open")])
    vehicle = make_fleet_vehicle(client, "TN54AA0005", speed=40,
                                 depot=towns["depot"])
    s1 = window_shipment(client, towns["depot"], towns["mid"])
    s2 = window_shipment(client, towns["mid"], towns["far"])

    data = propose(client).json()

    assert data["summary"]["shipments_planned"] == 2
    assert data["summary"]["vehicles_used"] == 1
    stops = data["vehicles"][0]["stops"]
    assert [st["shipment_id"] for st in stops] == [s1, s2]
    assert stops[0]["drop_minute"] <= stops[1]["pickup_minute"] + 1
    assert data["vehicles"][0]["home_depot_id"] == towns["depot"]


def test_closed_road_makes_shipment_unreachable(client):
    towns = make_world(client, [("depot", "mid", 50, "open"),
                                ("mid", "far", 50, "closed")])
    make_fleet_vehicle(client, "TN55AA0006", speed=40, depot=towns["depot"])
    ok_s = window_shipment(client, towns["depot"], towns["mid"])
    stuck = window_shipment(client, towns["mid"], towns["far"])

    data = propose(client).json()

    ids = {p["shipment_id"] for p in data["proposals"]}
    assert ids == {ok_s}
    reason = next(r["reason"] for r in data["skipped"]
                  if r["shipment_id"] == stuck)
    assert "No open road" in reason


def test_shipment_window_validation(client):
    origin = client.post("/locations", json={
        "name": "A", "location_type": "depot",
        "latitude": 11.0, "longitude": 78.0, "is_depot": True}).json()["id"]
    dest = client.post("/locations", json={
        "name": "B", "location_type": "warehouse",
        "latitude": 11.1, "longitude": 78.1}).json()["id"]
    response = client.post("/shipments", json={
        "description": "Bad window", "weight_kg": 100,
        "origin_id": origin, "destination_id": dest,
        "ready_minute": 500, "deadline_minute": 400})

    assert response.status_code == 400
    assert "deadline" in response.json()["detail"]
