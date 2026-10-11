import os

import psycopg

from audit import verify_chain
from datagen import generate_world


def run_datagen(client, body=None):
    return client.post("/datagen", json=body or {})


def test_generates_a_full_small_world(client):
    r = run_datagen(client, {"scale": "small", "seed": 7})

    assert r.status_code == 200
    data = r.json()
    assert data["locations"] == 6
    assert data["roads"] >= 5  # at least a spanning tree
    assert data["vehicles"] == 4
    assert data["drivers"] == 4
    assert data["shipments"] == 8
    assert len(client.get("/locations").json()) == 6
    assert len(client.get("/vehicles").json()) == 4
    assert len(client.get("/drivers").json()) == 4
    assert len(client.get("/shipments").json()) == 8
    assert len(client.get("/road-edges").json()) == data["roads"]


def test_same_seed_builds_same_world(client):
    run_datagen(client, {"scale": "small", "seed": 99})
    first = client.get("/vehicles").json()

    # a fresh empty database gives the identical second world
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(
            "TRUNCATE audit_log, approvals, incident_evidence, incidents, "
            "zones, road_edges, assignments, shipments, drivers, vehicles, "
            "locations RESTART IDENTITY")
    run_datagen(client, {"scale": "small", "seed": 99})
    second = client.get("/vehicles").json()

    assert [v["plate"] for v in first] == [v["plate"] for v in second]


def test_guard_refuses_without_confirm(client):
    run_datagen(client, {"scale": "small"})
    r = run_datagen(client, {"scale": "small"})
    assert r.status_code == 409
    assert "confirm" in r.json()["detail"]
    # ...but confirm=true appends (different seed, so no plate clashes)
    r = run_datagen(client, {"scale": "small", "confirm": True, "seed": 2})
    assert r.status_code == 200
    assert len(client.get("/locations").json()) == 12


def test_generated_network_is_routable(client):
    run_datagen(client, {"scale": "medium", "seed": 5})
    locs = client.get("/locations").json()
    a = locs[0]["id"]
    for dest in locs[1:]:
        r = client.get(f"/route?from_id={a}&to_id={dest['id']}")
        assert r.status_code == 200, f"no route to {dest['name']}"


def test_generated_shipments_reference_real_locations(client):
    run_datagen(client, {"scale": "small", "seed": 3})
    loc_ids = {loc["id"] for loc in client.get("/locations").json()}
    for s in client.get("/shipments").json():
        assert s["origin_id"] in loc_ids
        assert s["destination_id"] in loc_ids
        assert s["origin_id"] != s["destination_id"]
        assert s["weight_kg"] > 0


def test_generated_world_works_with_the_planner(client):
    run_datagen(client, {"scale": "small", "seed": 11})
    data = client.post("/planner/propose", json={}).json()
    assert data["summary"]["shipments_considered"] == 8
    assert data["summary"]["eligible_vehicles"] >= 0


def test_datagen_run_is_audited_and_chain_verifies(client):
    run_datagen(client, {"scale": "small", "seed": 1})
    logged = [row for row in client.get("/audit?limit=20").json()
              if row["action"] == "datagen.run"]
    assert len(logged) == 1
    assert logged[0]["actor_type"] == "datagen"
    assert logged[0]["details"]["scale"] == "small"
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        assert verify_chain(conn)["ok"] is True


def test_generator_module_is_deterministic():
    a = generate_world(123, 6, 4, 4, 8)
    b = generate_world(123, 6, 4, 4, 8)
    assert a == b
    c = generate_world(124, 6, 4, 4, 8)
    assert a["locations"] != c["locations"]
