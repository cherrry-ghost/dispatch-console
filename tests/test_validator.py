from validator import validate_route


def loc(lat, lng):
    return {"latitude": lat, "longitude": lng}


ZONES = [{
    "id": 1, "name": "War zone", "center_lat": 13.05, "center_lng": 80.00,
    "radius_km": 4, "active": True,
}]

LOCS = {
    1: loc(13.10, 80.00),  # north point
    2: loc(13.05, 80.00),  # inside the zone (centre)
    3: loc(13.00, 80.00),  # south point
    4: loc(13.05, 80.10),  # east, safely outside
}


def test_route_through_zone_has_violations():
    violations = validate_route([1, 2, 3], ZONES, LOCS)
    assert len(violations) == 2  # both legs touch the zone
    assert all(v["rule"] == "forbidden_zone" for v in violations)
    assert violations[0]["zone"] == "War zone"


def test_clean_route_has_no_violations():
    assert validate_route([1, 4, 3], ZONES, LOCS) == []


def test_long_leg_cutting_through_zone_is_caught_by_midpoint():
    # 1 -> 3 is one straight ~11 km leg passing right over the zone centre,
    # even though both endpoints are outside the 4 km circle.
    violations = validate_route([1, 3], ZONES, LOCS)
    assert len(violations) == 1


def test_empty_zones_and_short_paths_are_clean():
    assert validate_route([1, 3], [], LOCS) == []
    assert validate_route([2], ZONES, LOCS) == []  # single node, no legs
