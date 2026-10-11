"""Event pipeline and geofencing (Phase 8B).

The pipeline is one small contract: something notable happens -> emit()
stores it in the events table and hub.broadcast() pushes it to every
console on /ws/events.

Geofencing sits on top: every vehicle position is checked against ACTIVE
zones. 'forbidden' zones behave as before (routing refuses them, entering
one is a warning in the audit ledger); 'geofence_alert' zones never block
anything but raise geofence.enter / geofence.exit events when a vehicle
crosses their boundary. Crossings fire on transitions, not every ping.
"""
import json

from psycopg.types.json import Jsonb
from zones import point_in_zone

# in-memory enter/exit state as (vehicle_id, zone_id) pairs, so a crossing
# fires exactly once per boundary touch
_zone_state = set()


class EventHub:
    """Console viewers on /ws/events, plus the broadcast they listen to."""

    def __init__(self):
        self.viewers = set()

    async def connect(self, ws):
        await ws.accept()
        self.viewers.add(ws)

    def disconnect(self, ws):
        self.viewers.discard(ws)

    async def broadcast(self, event: dict):
        for ws in list(self.viewers):
            try:
                await ws.send_json(event)
            except Exception:
                self.viewers.discard(ws)


hub = EventHub()


def emit(conn, kind, severity="info", vehicle_id=None, shipment_id=None,
         zone_id=None, details=None):
    """Persist one event; returns it as a JSON-ready dict for broadcasting."""
    row = conn.execute(
        "INSERT INTO events (kind, severity, vehicle_id, shipment_id, "
        "zone_id, details) VALUES (%s, %s, %s, %s, %s, %s) "
        "RETURNING id, kind, severity, vehicle_id, shipment_id, zone_id, "
        "details, created_at",
        (kind, severity, vehicle_id, shipment_id, zone_id,
         Jsonb(details or {})),
    ).fetchone()
    event = dict(row)
    # isoformat string so the payload is JSON-safe for WebSocket frames
    event["created_at"] = event["created_at"].isoformat()
    return event


def check_geofences(conn, vehicle_id, lat, lng):
    """Check one position against all active zones.

    Returns (forbidden_alert, crossings): forbidden_alert keeps the exact
    shape the live-tracking code already broadcasts (or None); crossings is
    the list of (kind, severity, zone) boundary transitions to emit.
    """
    zones = conn.execute(
        "SELECT * FROM zones WHERE active ORDER BY id").fetchall()
    inside = [z for z in zones if point_in_zone(lat, lng, z)]
    inside_ids = {z["id"] for z in inside}
    prev_ids = {zid for vid, zid in _zone_state if vid == vehicle_id}

    alert, crossings = None, []
    for z in inside:
        if z["id"] in prev_ids:
            continue
        if z["zone_type"] == "forbidden":
            if alert is None:
                alert = {"zone_id": z["id"], "zone_name": z["name"],
                         "radius_km": z["radius_km"]}
            crossings.append(("vehicle.in_zone", "warning", z))
        else:
            crossings.append(("geofence.enter", "warning", z))
    for zid in prev_ids - inside_ids:
        z = next((z for z in zones if z["id"] == zid), None)
        if z is None:
            continue  # zone was deactivated or removed meanwhile
        kind = ("vehicle.left_zone" if z["zone_type"] == "forbidden"
                else "geofence.exit")
        crossings.append((kind, "info", z))

    _zone_state.difference_update({(vehicle_id, zid) for zid in prev_ids})
    _zone_state.update({(vehicle_id, zid) for zid in inside_ids})
    return alert, crossings
