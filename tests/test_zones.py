from test_road_edges import make_locations, road


def make_zone(client, name, lat, lng, radius_km):
    return client.post("/zones", json={
        "name": name, "center_lat": lat, "center_lng": lng,
        "radius_km": radius_km,
    })


def chain_world(client):
    """A -> B -> C along one straight road line (A north of B north of C)."""
    a = client.post("/locations", json={
        "name": "A", "location_type": "depot",
        "latitude": 13.10, "longitude": 80.00}).json()["id"]
    b = client.post("/locations", json={
        "name": "B", "location_type": "depot",
        "latitude": 13.05, "longitude": 80.00}).json()["id"]
    c = client.post("/locations", json={
        "name": "C", "location_type": "warehouse",
        "latitude": 13.00, "longitude": 80.00}).json()["id"]
    road(client, a, b, 6)
    road(client, b, c, 6)
    return a, b, c


def detour_world(client):
    """chain_world plus a long safe detour A -> D -> C east of the zone."""
    a, b, c = chain_world(client)
    d = client.post("/locations", json={
        "name": "D", "location_type": "depot",
        "latitude": 13.05, "longitude": 80.10}).json()["id"]
    road(client, a, d, 15)
    road(client, d, c, 15)
    return a, b, c, d


def test_zone_create_and_list_is_audited(client):
    r = make_zone(client, "War zone", 13.05, 80.00, 5)
    assert r.status_code == 200
    assert r.json()["active"] is True
    zones = client.get("/zones").json()
    assert len(zones) == 1
    assert zones[0]["name"] == "War zone"
    actions = [a["action"] for a in client.get("/audit?limit=10").json()]
    assert "zone.create" in actions


def test_zone_status_change_is_audited(client):
    zone_id = make_zone(client, "Flood area", 13.05, 80.00, 5).json()["id"]
    r = client.post(f"/zones/{zone_id}/status", json={"active": False})
    assert r.status_code == 200
    assert r.json()["active"] is False
    assert client.get("/zones").json()[0]["active"] is False
    actions = [a["action"] for a in client.get("/audit?limit=10").json()]
    assert "zone.deactivate" in actions


def test_unknown_zone_status_is_404(client):
    r = client.post("/zones/999/status", json={"active": False})
    assert r.status_code == 404


def test_invalid_zone_rejected(client):
    assert make_zone(client, "Bad", 13.0, 80.0, 0).status_code == 422
    assert make_zone(client, "Bad", 95.0, 80.0, 5).status_code == 422


def test_route_goes_around_forbidden_zone(client):
    a, b, c, d = detour_world(client)
    make_zone(client, "Conflict zone", 13.05, 80.00, 4)  # covers A-B and B-C

    r = client.get(f"/route?from_id={a}&to_id={c}").json()

    assert r["path"] == [a, d, c]
    assert r["label"] == "safe"
    assert r["blocked_by_zones"] == ["Conflict zone"]


def test_no_route_when_zone_blocks_only_path_explains_why(client):
    a, b, c = chain_world(client)
    make_zone(client, "Conflict zone", 13.05, 80.00, 4)

    r = client.get(f"/route?from_id={a}&to_id={c}")

    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["blocked_by_zones"] == ["Conflict zone"]
    assert any("Conflict zone" in reason for reason in detail["reasons"])
    outcomes = [row["outcome"] for row in client.get("/audit?limit=10").json()
                if row["action"] == "route.request"]
    assert outcomes == ["blocked"]


def test_inactive_zone_opens_road_again(client):
    a, b, c, d = detour_world(client)
    zone_id = make_zone(client, "Conflict zone", 13.05, 80.00, 4).json()["id"]
    client.post(f"/zones/{zone_id}/status", json={"active": False})

    r = client.get(f"/route?from_id={a}&to_id={c}").json()

    assert r["path"] == [a, b, c]  # short road is back


def test_high_risk_route_is_flagged_for_approval(client):
    a, b, c = chain_world(client)
    road(client, a, c, 12, risk=80)  # tempting but high risk

    r = client.get(f"/route?from_id={a}&to_id={c}").json()

    assert r["path"] == [a, c]
    assert r["label"] == "needs_approval"
    flagged = [row for row in client.get("/audit?limit=10").json()
               if row["action"] == "route.request"]
    assert flagged and flagged[0]["outcome"] == "allowed"
    assert "manager approval" in flagged[0]["details"]["note"]


def test_moderate_risk_route_is_caution(client):
    a, b, c = chain_world(client)
    road(client, a, c, 8, risk=20)  # shortest option, some risk

    r = client.get(f"/route?from_id={a}&to_id={c}").json()

    assert r["path"] == [a, c]
    assert r["label"] == "caution"
