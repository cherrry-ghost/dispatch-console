"""Independent validator: re-checks a FINAL route against raw zone data.

This module runs AFTER routing, as a second line of defence. It deliberately
uses a different method from zones.py (endpoints + midpoint sampled with
haversine, instead of segment projection) so a geometry bug in the filter
cannot repeat itself here unnoticed.
"""
from zones import haversine_km


def _midpoint(a, b):
    return ((a["latitude"] + b["latitude"]) / 2,
            (a["longitude"] + b["longitude"]) / 2)


def validate_route(path, zones, locations):
    """Return a list of violations; empty means the route is clean.

    path: list of location ids. zones: active zone rows.
    locations: dict of id -> row with latitude/longitude.
    """
    violations = []
    for a_id, b_id in zip(path, path[1:]):
        a, b = locations[a_id], locations[b_id]
        probe_points = [
            (a["latitude"], a["longitude"]),
            _midpoint(a, b),
            (b["latitude"], b["longitude"]),
        ]
        for z in zones:
            center = (z["center_lat"], z["center_lng"])
            touches = any(
                haversine_km(p[0], p[1], center[0], center[1]) <= z["radius_km"]
                for p in probe_points
            )
            if touches:
                violations.append({
                    "rule": "forbidden_zone",
                    "zone": z["name"],
                    "leg": [a_id, b_id],
                    "message": (
                        f"Road {a_id} -> {b_id} passes through forbidden "
                        f"zone '{z['name']}'."
                    ),
                })
    return violations
