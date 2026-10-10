from test_road_edges import road


def risky_world(client):
    """A shortcut A-B (risky) plus a safe detour A-C-B (longer, clean).

    6 km at risk 90 weighs 11.4 risk-weighted; the 10 km detour weighs 10,
    so 'prefer=safe' picks the detour while the default still picks A-B.
    """
    a = client.post("/locations", json={
        "name": "A", "location_type": "depot",
        "latitude": 13.10, "longitude": 80.00}).json()["id"]
    b = client.post("/locations", json={
        "name": "B", "location_type": "warehouse",
        "latitude": 13.10, "longitude": 80.05}).json()["id"]
    c = client.post("/locations", json={
        "name": "C", "location_type": "depot",
        "latitude": 13.00, "longitude": 80.02}).json()["id"]
    road(client, a, b, 6, risk=90)    # short but dangerous
    road(client, a, c, 5, risk=0)
    road(client, c, b, 5, risk=0)
    return a, b, c


def test_fast_route_takes_shortcut_and_flags_approval(client):
    a, b, c = risky_world(client)
    r = client.get(f"/route?from_id={a}&to_id={b}").json()
    assert r["path"] == [a, b]
    assert r["max_risk"] == 90
    assert r["label"] == "needs_approval"


def test_safe_route_takes_the_long_clean_way(client):
    a, b, c = risky_world(client)
    r = client.get(f"/route?from_id={a}&to_id={b}&prefer=safe").json()
    assert r["path"] == [a, c, b]
    assert r["distance_km"] == 10.0
    assert r["max_risk"] == 0
    assert r["label"] == "safe"


def test_ranked_routes_lists_both_options(client):
    a, b, c = risky_world(client)
    r = client.get(f"/routes?from_id={a}&to_id={b}").json()
    assert len(r["routes"]) == 2
    assert r["routes"][0]["weighted_cost"] <= r["routes"][1]["weighted_cost"]
    assert {tuple(x["path"]) for x in r["routes"]} == {(a, b), (a, c, b)}
    assert all(x["label"] for x in r["routes"])
    # the 10 km detour weighs 10 vs 11.4 for the risky shortcut
    assert r["recommended"]["path"] == [a, c, b]
    assert r["routes"][0]["label"] == "safe"
    assert r["routes"][1]["label"] == "needs_approval"


def test_use_request_on_safe_route_needs_no_approval(client):
    a, b, c = risky_world(client)
    r = client.post("/route/use-request",
                    json={"from_id": a, "to_id": b, "prefer": "safe"})
    assert r.status_code == 200
    assert r.json()["status"] == "not_required"
    assert client.get("/approvals").json() == []


def test_use_request_on_high_risk_route_goes_to_manager(client):
    a, b, c = risky_world(client)
    r = client.post("/route/use-request",
                    json={"from_id": a, "to_id": b, "prefer": "fast"})
    assert r.status_code == 202
    assert r.json()["status"] == "pending_approval"
    pending = client.get("/approvals?status=pending").json()
    assert len(pending) == 1
    assert pending[0]["action"] == "route.use"
    logged = [row for row in client.get("/audit?limit=20").json()
              if row["action"] == "route.use_request"]
    assert logged and logged[0]["outcome"] == "pending"


def test_manager_can_approve_route_use(client):
    a, b, c = risky_world(client)
    approval_id = client.post(
        "/route/use-request",
        json={"from_id": a, "to_id": b, "prefer": "fast"},
    ).json()["approval_id"]

    r = client.post(f"/approvals/{approval_id}/decision",
                    json={"decision": "approved", "note": "escort ready"})

    assert r.status_code == 200
    row = client.get("/approvals").json()[0]
    assert row["status"] == "approved"
    assert row["decided_by"] == "manager"
    logged = [x for x in client.get("/audit?limit=20").json()
              if x["action"] == "approval.approved"]
    assert logged[0]["entity_type"] == "route"


def test_manager_can_reject_route_use(client):
    a, b, c = risky_world(client)
    approval_id = client.post(
        "/route/use-request",
        json={"from_id": a, "to_id": b, "prefer": "fast"},
    ).json()["approval_id"]
    r = client.post(f"/approvals/{approval_id}/decision",
                    json={"decision": "rejected", "note": "too risky"})
    assert r.status_code == 200
    assert client.get("/approvals").json()[0]["status"] == "rejected"


def test_approval_blocked_when_route_later_becomes_unusable(client):
    a, b, c = risky_world(client)
    edge_id = client.get("/road-edges").json()[0]["id"]  # the risky A-B road
    approval_id = client.post(
        "/route/use-request",
        json={"from_id": a, "to_id": b, "prefer": "fast"},
    ).json()["approval_id"]

    # a verified incident closes the shortcut after the request
    client.post("/incidents", json={
        "description": "Protest", "incident_type": "protest",
        "road_edge_id": edge_id})
    client.post(f"/incidents/1/verify")

    r = client.post(f"/approvals/{approval_id}/decision",
                    json={"decision": "approved", "note": ""})

    assert r.status_code == 422
    assert "no longer usable" in r.json()["detail"]["message"]
    row = client.get("/approvals").json()[0]
    assert row["status"] == "pending"  # stays open for a later decision
