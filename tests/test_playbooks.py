import os

import psycopg

from audit import verify_chain


def make_world(client):
    """A depot, two towns, three roads (depot-mid-far plus a depot-far link)."""
    depot = client.post("/locations", json={
        "name": "Central Depot", "location_type": "depot",
        "latitude": 11.0, "longitude": 78.0, "is_depot": True}).json()["id"]
    mid = client.post("/locations", json={
        "name": "Midtown", "location_type": "city",
        "latitude": 11.3, "longitude": 78.2}).json()["id"]
    far = client.post("/locations", json={
        "name": "Farville", "location_type": "city",
        "latitude": 11.6, "longitude": 78.4}).json()["id"]
    edges = {}
    for a, b, km in [("depot", "mid", 40), ("mid", "far", 50),
                     ("depot", "far", 70)]:
        towns = {"depot": depot, "mid": mid, "far": far}
        edges[f"{a}-{b}"] = client.post("/road-edges", json={
            "from_location_id": towns[a], "to_location_id": towns[b],
            "distance_km": km}).json()["id"]
    client.post("/shipments", json={
        "description": "Rice", "weight_kg": 1000,
        "origin_id": depot, "destination_id": far})
    return {"depot": depot, "mid": mid, "far": far, "edges": edges}


def test_scenario_combines_several_disruptions(client):
    world = make_world(client)

    r = client.post("/simulate/scenario", json={"actions": [
        {"kind": "close_road", "edge_id": world["edges"]["depot-far"]},
        {"kind": "close_road", "edge_id": world["edges"]["mid-far"]},
        {"kind": "add_zone", "zone": {"name": "War zone",
                                      "center_lat": 11.3,
                                      "center_lng": 78.2,
                                      "radius_km": 25}},
        {"kind": "raise_risk", "edge_id": world["edges"]["depot-mid"],
         "risk_score": 80},
    ]})

    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["shipments_simulated"] == 1
    assert data["network"]["roads_closed_by_scenario"] == 2
    assert 0.0 <= data["network"]["largest_connected_share"] <= 1.0
    # the shipment from depot to far must suffer: both its roads are gone
    assert data["impact"][0]["status"] in ("broken", "rerouted")
    # reality untouched
    edges = client.get("/road-edges").json()
    assert all(e["status"] == "open" for e in edges)
    assert client.get("/zones").json() == []
    rows = [x for x in client.get("/audit?limit=20").json()
            if x["action"] == "simulation.scenario"]
    assert len(rows) == 1
    assert len(rows[0]["details"]["actions"]) == 4


def test_scenario_rejects_bad_actions(client):
    world = make_world(client)

    r = client.post("/simulate/scenario", json={"actions": [
        {"kind": "close_road", "edge_id": 9999}]})
    assert r.status_code == 404

    r = client.post("/simulate/scenario", json={"actions": [
        {"kind": "close_road"}]})
    assert r.status_code == 422

    r = client.post("/simulate/scenario", json={"actions": [
        {"kind": "raise_risk", "edge_id": world["edges"]["depot-mid"]}]})
    assert r.status_code == 422

    r = client.post("/simulate/scenario", json={"actions": [
        {"kind": "add_zone"}]})
    assert r.status_code == 422

    r = client.post("/simulate/scenario", json={"actions": []})
    assert r.status_code == 422


def test_playbook_is_validated_by_simulation_before_saving(client):
    world = make_world(client)

    r = client.post("/playbooks", json={
        "name": "Highway blockade",
        "description": "Protest closes the direct highway; use the bypass",
        "actions": [{"kind": "close_road",
                     "edge_id": world["edges"]["depot-far"]}],
    })

    assert r.status_code == 200
    data = r.json()
    assert data["summary"]["shipments_simulated"] == 1
    assert data["summary"]["rerouted"] == 1  # bypass through Midtown exists
    saved = client.get("/playbooks").json()
    assert len(saved) == 1
    assert saved[0]["name"] == "Highway blockade"
    assert saved[0]["last_impact"]["summary"]["rerouted"] == 1
    rows = [x for x in client.get("/audit?limit=20").json()
            if x["action"] == "playbook.create"]
    assert rows and rows[0]["actor_type"] == "manager"


def test_playbook_with_broken_scenario_is_rejected(client):
    make_world(client)

    r = client.post("/playbooks", json={
        "name": "Bad playbook",
        "actions": [{"kind": "close_road", "edge_id": 9999}]})

    assert r.status_code == 404
    assert client.get("/playbooks").json() == []


def test_duplicate_playbook_name_is_409(client):
    world = make_world(client)
    body = {"name": "Same name",
            "actions": [{"kind": "close_road",
                         "edge_id": world["edges"]["depot-far"]}]}
    assert client.post("/playbooks", json=body).status_code == 200
    assert client.post("/playbooks", json=body).status_code == 409


def test_playbook_test_refreshes_recorded_impact(client):
    world = make_world(client)
    playbook = client.post("/playbooks", json={
        "name": "Blockade", "description": "",
        "actions": [{"kind": "close_road",
                     "edge_id": world["edges"]["depot-far"]}]}).json()["id"]

    r = client.post(f"/playbooks/{playbook}/test")

    assert r.status_code == 200
    assert r.json()["summary"]["rerouted"] == 1
    rows = [x for x in client.get("/audit?limit=20").json()
            if x["action"] == "playbook.test"]
    assert len(rows) == 1


def test_apply_playbook_changes_the_real_world(client):
    world = make_world(client)
    playbook = client.post("/playbooks", json={
        "name": "War response",
        "description": "Close the highway and declare a war zone",
        "actions": [
            {"kind": "close_road", "edge_id": world["edges"]["depot-far"]},
            {"kind": "add_zone", "zone": {"name": "Applied war zone",
                                          "center_lat": 11.0,
                                          "center_lng": 78.0,
                                          "radius_km": 20}},
        ]}).json()["id"]

    r = client.post(f"/playbooks/{playbook}/apply")

    assert r.status_code == 200
    assert len(r.json()["applied"]) == 2
    assert r.json()["skipped"] == []
    edges = {e["id"]: e for e in client.get("/road-edges").json()}
    assert edges[world["edges"]["depot-far"]]["status"] == "closed"
    zones = client.get("/zones").json()
    assert len(zones) == 1 and zones[0]["active"] is True
    # the zone around the depot blocks every road out of it
    route = client.get(f"/route?from_id={world['depot']}&to_id={world['far']}")
    assert route.status_code == 404
    rows = [x for x in client.get("/audit?limit=20").json()
            if x["action"] == "playbook.apply"]
    assert rows and rows[0]["actor_type"] == "manager"
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        assert verify_chain(conn)["ok"] is True


def test_apply_is_idempotent(client):
    world = make_world(client)
    playbook = client.post("/playbooks", json={
        "name": "Close once",
        "actions": [{"kind": "close_road",
                     "edge_id": world["edges"]["depot-far"]}]}).json()["id"]

    first = client.post(f"/playbooks/{playbook}/apply")
    second = client.post(f"/playbooks/{playbook}/apply")

    assert first.status_code == 200 and second.status_code == 200
    assert len(second.json()["applied"]) == 0
    assert second.json()["skipped"][0]["reason"] == "road already closed"


def test_apply_unknown_playbook_is_404(client):
    assert client.post("/playbooks/999/apply").status_code == 404
