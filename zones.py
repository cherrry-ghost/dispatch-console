"""Forbidden zones: circles on the map that no route may touch.

Deterministic geography only - no AI. Zones are removed from the road network
BEFORE routing, so the router never even sees a forbidden road.
"""
from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1, lng1, lat2, lng2):
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lng2 - lng1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def _local_km(lat, lng, lat0, lng0):
    """Flat projection (km) around a reference point, fine for zone distances."""
    x = radians(lng - lng0) * EARTH_RADIUS_KM * cos(radians(lat0))
    y = radians(lat - lat0) * EARTH_RADIUS_KM
    return x, y


def segment_distance_km(point_a, point_b, center):
    """Shortest distance in km from the segment a-b to the center point."""
    ax, ay = _local_km(point_a[0], point_a[1], center[0], center[1])
    bx, by = _local_km(point_b[0], point_b[1], center[0], center[1])
    abx, aby = bx - ax, by - ay
    length_sq = abx * abx + aby * aby
    if length_sq == 0:
        return sqrt(ax * ax + ay * ay)
    t = max(0.0, min(1.0, -(ax * abx + ay * aby) / length_sq))
    px, py = ax + t * abx, ay + t * aby
    return sqrt(px * px + py * py)


def edge_hits_zone(from_loc, to_loc, zone):
    """True if the road between two locations touches the zone circle."""
    center = (zone["center_lat"], zone["center_lng"])
    return segment_distance_km(
        (from_loc["latitude"], from_loc["longitude"]),
        (to_loc["latitude"], to_loc["longitude"]),
        center,
    ) <= zone["radius_km"]


def split_edges_by_zones(edges, zones, locations):
    """Separate road rows into (allowed, blocked).

    locations: dict of id -> row with latitude/longitude.
    blocked: list of (edge, zone) tuples so the caller can explain why a road
    disappeared from the network.
    """
    allowed, blocked = [], []
    for e in edges:
        hit = next(
            (z for z in zones if edge_hits_zone(locations[e["from_location_id"]],
                                                locations[e["to_location_id"]], z)),
            None,
        )
        if hit is None:
            allowed.append(e)
        else:
            blocked.append((e, hit))
    return allowed, blocked
