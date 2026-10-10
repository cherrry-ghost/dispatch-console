from routing import shortest_route


def edge(a, b, km, status="open", risk=0):
    return {"from_location_id": a, "to_location_id": b,
            "distance_km": km, "status": status, "risk_score": risk}


def test_picks_shortest_path():
    edges = [edge(1, 2, 10), edge(2, 3, 10), edge(1, 3, 50)]
    r = shortest_route(edges, 1, 3)
    assert r["path"] == [1, 2, 3]
    assert r["distance_km"] == 20


def test_closed_road_is_ignored():
    edges = [edge(1, 2, 10), edge(2, 3, 10, status="closed"), edge(1, 3, 50)]
    r = shortest_route(edges, 1, 3)
    assert r["path"] == [1, 3]
    assert r["distance_km"] == 50


def test_no_route_returns_none():
    edges = [edge(1, 2, 10), edge(2, 3, 10, status="closed")]
    assert shortest_route(edges, 1, 3) is None


def test_max_risk_reported():
    edges = [edge(1, 2, 10, risk=20), edge(2, 3, 10, risk=70)]
    assert shortest_route(edges, 1, 3)["max_risk"] == 70
