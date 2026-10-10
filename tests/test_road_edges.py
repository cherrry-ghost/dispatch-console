def make_locations(client, n):
    ids = []
    for i in range(n):
        r = client.post("/locations", json={
            "name": f"L{i}", "location_type": "depot",
            "latitude": 13.0 + i, "longitude": 80.0 + i})
        ids.append(r.json()["id"])
    return ids


def road(client, a, b, km, status="open", risk=0):
    return client.post("/road-edges", json={
        "from_location_id": a, "to_location_id": b,
        "distance_km": km, "status": status, "risk_score": risk})


def test_create_and_list_road(client):
    a, b = make_locations(client, 2)
    assert road(client, a, b, 100).status_code == 200
    assert len(client.get("/road-edges").json()) == 1


def test_reversed_pair_is_normalised_and_duplicate_rejected(client):
    a, b = make_locations(client, 2)
    r = road(client, b, a, 100)
    assert r.json()["from_location_id"] == a
    assert road(client, a, b, 90).status_code == 409


def test_unknown_location_rejected(client):
    (a,) = make_locations(client, 1)
    assert road(client, a, 999, 10).status_code == 400


def test_route_endpoint_shortest_and_closed(client):
    a, b, c = make_locations(client, 3)
    road(client, a, b, 10)
    road(client, b, c, 10)
    road(client, a, c, 50)
    r = client.get(f"/route?from_id={a}&to_id={c}")
    assert r.json()["path"] == [a, b, c]
    client.post("/road-edges", json={})  # invalid body, must not break anything


def test_route_404_when_no_open_road(client):
    a, b = make_locations(client, 2)
    road(client, a, b, 10, status="closed")
    assert client.get(f"/route?from_id={a}&to_id={b}").status_code == 404
