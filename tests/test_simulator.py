from audit import verify_chain
import os
import psycopg

from test_road_edges import road


def world(client, with_detour=True):
    """A - B - C chain southwards, optionally with a safe detour via D."""
    ids = {}
    coords = {
        "A": (13.10, 80.00), "B": (13.05, 80.00),
        "C": (13.00, 80.00), "D": (13.05, 80.10),
    }
    for name, (lat, lng) in coords.items():
        if name == "D" and not with_detour:
            continue
        ids[name] = client.post("/locations", json={
            "name": name, "location_type": "depot",
            "latitude": lat, "longitude": lng}).json()["id"]
    road(client, ids["A"], ids["B"], 6)
    road(client, ids["B"], ids["C"], 6)
    if with_detour:
        road(client, ids["A"], ids["D"], 15)
        road(client, ids["D"], ids["C"], 15)
    edges = {e["id"]: e for e in client.get("/road-edges").json()}
    edge_ab = next(e["id"] for e in edges.values()
                   if {e["from_location_id"], e["to_location_id"]} ==
                   {ids["A"], ids["B"]})
    return ids, edge_ab


def make_shipment_od(client, origin, destination):
    return client.post("/shipments", json={
        "description": "goods", "weight_kg": 1000,
        "origin_id": origin, "destination_id": destination,
    }).json()["id"]


def simulate(client, body):
    return client.post("/simulate/disruption", json=body).json()


def test_close_road_forces_a_longer_route(client):
    ids, edge_ab = world(client)
    s = make_shipment_od(client, ids["A"], ids["C"])

    data = simulate(client, {"kind": "close_road", "edge_id": edge_ab})

    assert data["summary"]["rerouted"] == 1
    row = next(i for i in data["impact"] if i["shipment_id"] == s)
    assert row["status"] == "rerouted"
    assert row["before"]["path"] == [ids["A"], ids["B"], ids["C"]]
    assert row["before"]["distance_km"] == 12.0
    assert row["after"]["path"] == [ids["A"], ids["D"], ids["C"]]
    assert row["delta_km"] == 18.0
    assert row["after"]["label"] == "safe"


def test_close_road_can_break_a_shipment_completely(client):
    ids, edge_ab = world(client, with_detour=False)
    s = make_shipment_od(client, ids["A"], ids["C"])

    data = simulate(client, {"kind": "close_road", "edge_id": edge_ab})

    assert data["summary"]["broken"] == 1
    row = next(i for i in data["impact"] if i["shipment_id"] == s)
    assert row["status"] == "broken"
    assert row["after"] is None
    assert row["delta_km"] is None


def test_add_zone_has_the_same_effect_without_touching_data(client):
    ids, edge_ab = world(client, with_detour=False)
    s = make_shipment_od(client, ids["A"], ids["C"])

    data = simulate(client, {"kind": "add_zone", "zone": {
        "name": "Hypothetical conflict", "center_lat": 13.05,
        "center_lng": 80.00, "radius_km": 4}})

    row = next(i for i in data["impact"] if i["shipment_id"] == s)
    assert row["status"] == "broken"
    # nothing real was created
    assert client.get("/zones").json() == []


def test_raise_risk_changes_the_safety_label(client):
    ids, edge_ab = world(client, with_detour=True)
    s = make_shipment_od(client, ids["A"], ids["C"])

    data = simulate(client, {"kind": "raise_risk", "edge_id": edge_ab,
                             "risk_score": 95})

    row = next(i for i in data["impact"] if i["shipment_id"] == s)
    assert row["status"] == "risk_changed"
    assert row["before"]["label"] == "safe"
    assert row["after"]["label"] == "needs_approval"
    assert row["after"]["path"] == row["before"]["path"]


def test_simulation_changes_nothing_real(client):
    ids, edge_ab = world(client)
    make_shipment_od(client, ids["A"], ids["C"])

    simulate(client, {"kind": "close_road", "edge_id": edge_ab})
    simulate(client, {"kind": "add_zone", "zone": {
        "name": "X", "center_lat": 13.05, "center_lng": 80.0,
        "radius_km": 3}})

    assert all(e["status"] == "open"
               for e in client.get("/road-edges").json())
    assert client.get("/zones").json() == []
    assert all(s["status"] == "pending"
               for s in client.get("/shipments").json())


def test_simulation_run_is_audited_and_chain_verifies(client):
    ids, edge_ab = world(client)
    make_shipment_od(client, ids["A"], ids["C"])

    simulate(client, {"kind": "close_road", "edge_id": edge_ab})

    logged = [r for r in client.get("/audit?limit=20").json()
              if r["action"] == "simulation.run"]
    assert len(logged) == 1
    assert logged[0]["details"]["summary"]["shipments_simulated"] == 1
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        assert verify_chain(conn)["ok"] is True


def test_unknown_road_is_404(client):
    world(client)
    r = client.post("/simulate/disruption",
                    json={"kind": "close_road", "edge_id": 999})
    assert r.status_code == 404


def test_missing_scenario_fields_are_422(client):
    ids, edge_ab = world(client)
    assert client.post("/simulate/disruption",
                       json={"kind": "close_road"}).status_code == 422
    assert client.post("/simulate/disruption",
                       json={"kind": "raise_risk",
                             "edge_id": edge_ab}).status_code == 422
    assert client.post("/simulate/disruption",
                       json={"kind": "add_zone"}).status_code == 422


def test_invalid_risk_score_is_422(client):
    ids, edge_ab = world(client)
    r = client.post("/simulate/disruption",
                    json={"kind": "raise_risk", "edge_id": edge_ab,
                          "risk_score": 150})
    assert r.status_code == 422
