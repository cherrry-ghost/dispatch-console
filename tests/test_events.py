"""Phase 8B: event pipeline and geofencing."""
import pytest

import events


@pytest.fixture(autouse=True)
def clean_tracking_state():
    events._zone_state.clear()
    yield
    events._zone_state.clear()


def make_vehicle(client):
    response = client.post("/vehicles", json={
        "plate": "TN 02 CD 5678", "vehicle_type": "truck",
        "capacity_kg": 8000,
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def feed(client, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"/events?{query}" if query else "/events")


def test_zone_type_is_stored_and_returned(client):
    forbidden = client.post("/zones", json={
        "name": "Old style", "center_lat": 12.0, "center_lng": 79.0,
        "radius_km": 10})
    assert forbidden.json()["zone_type"] == "forbidden"  # default
    alert = client.post("/zones", json={
        "name": "Watch only", "center_lat": 13.0, "center_lng": 80.0,
        "radius_km": 8, "zone_type": "geofence_alert"})
    assert alert.json()["zone_type"] == "geofence_alert"
    kinds = {z["name"]: z["zone_type"]
             for z in client.get("/zones").json()}
    assert kinds == {"Old style": "forbidden", "Watch only": "geofence_alert"}


def test_alert_zone_fires_enter_and_exit_events(client):
    vehicle_id = make_vehicle(client)
    zone = client.post("/zones", json={
        "name": "Protest area watch", "center_lat": 13.1,
        "center_lng": 80.2, "radius_km": 5,
        "zone_type": "geofence_alert"}).json()

    inside = client.post(f"/vehicles/{vehicle_id}/position", json={
        "latitude": 13.1005, "longitude": 80.2005})
    assert inside.json()["zone_alert"] is None  # never blocks, never flags

    rows = feed(client, kind="geofence.enter").json()
    assert len(rows) == 1 and rows[0]["zone_id"] == zone["id"]
    assert rows[0]["vehicle_id"] == vehicle_id
    assert rows[0]["severity"] == "warning"

    # a second ping inside fires nothing new (transitions, not pings)
    client.post(f"/vehicles/{vehicle_id}/position", json={
        "latitude": 13.101, "longitude": 80.201})
    assert len(feed(client, kind="geofence.enter").json()) == 1

    out = client.post(f"/vehicles/{vehicle_id}/position", json={
        "latitude": 14.9, "longitude": 81.9})
    assert out.json()["zone_alert"] is None
    exits = feed(client, kind="geofence.exit").json()
    assert len(exits) == 1 and exits[0]["zone_id"] == zone["id"]
    assert exits[0]["severity"] == "info"

    # crossing again fires a fresh pair
    client.post(f"/vehicles/{vehicle_id}/position", json={
        "latitude": 13.1005, "longitude": 80.2005})
    assert len(feed(client, kind="geofence.enter").json()) == 2


def test_alert_zones_do_not_block_routing(client):
    # two locations 30 km apart with one direct road THROUGH the zone
    a = client.post("/locations", json={
        "name": "Origin town", "location_type": "depot",
        "latitude": 13.10, "longitude": 80.20, "is_depot": True}).json()["id"]
    b = client.post("/locations", json={
        "name": "Dest town", "location_type": "warehouse",
        "latitude": 13.20, "longitude": 80.30}).json()["id"]
    client.post("/road-edges", json={
        "from_location_id": a, "to_location_id": b, "distance_km": 30})

    client.post("/zones", json={
        "name": "Watchful circle", "center_lat": 13.15,
        "center_lng": 80.25, "radius_km": 20,
        "zone_type": "geofence_alert"})
    with_alert = client.get(f"/routes?from_id={a}&to_id={b}")
    assert with_alert.status_code == 200, with_alert.text
    body = with_alert.json()
    assert len(body["routes"]) >= 1        # the road survives
    assert body["blocked_by_zones"] == []  # an alert zone never blocks

    # the identical zone as forbidden: the road is pulled from the network
    client.post("/zones", json={
        "name": "Forbidden twin", "center_lat": 13.15,
        "center_lng": 80.25, "radius_km": 20})
    with_forbidden = client.get(f"/routes?from_id={a}&to_id={b}")
    assert with_forbidden.status_code == 404
    assert "Forbidden twin" in str(with_forbidden.json()["detail"])


def test_forbidden_zone_still_warns_and_audits(client):
    vehicle_id = make_vehicle(client)
    client.post("/zones", json={
        "name": "War zone", "center_lat": 13.1, "center_lng": 80.2,
        "radius_km": 5})
    inside = client.post(f"/vehicles/{vehicle_id}/position", json={
        "latitude": 13.1005, "longitude": 80.2005})
    assert inside.json()["zone_alert"]["zone_name"] == "War zone"
    audits = [a for a in client.get("/audit").json()
              if a["action"] == "vehicle.in_zone"]
    assert len(audits) == 1
    # leaving fires the calm pipeline event, not another audit warning
    client.post(f"/vehicles/{vehicle_id}/position", json={
        "latitude": 15.0, "longitude": 81.0})
    left = feed(client, kind="vehicle.left_zone").json()
    assert len(left) == 1
    audits = [a for a in client.get("/audit").json()
              if a["action"] == "vehicle.in_zone"]
    assert len(audits) == 1  # unchanged


def test_road_status_and_assignment_emissions(client):
    a = client.post("/locations", json={
        "name": "A depot", "location_type": "depot",
        "latitude": 11.0, "longitude": 78.0, "is_depot": True}).json()["id"]
    b = client.post("/locations", json={
        "name": "B warehouse", "location_type": "warehouse",
        "latitude": 11.1, "longitude": 78.1}).json()["id"]
    edge = client.post("/road-edges", json={
        "from_location_id": a, "to_location_id": b,
        "distance_km": 30}).json()["id"]
    vehicle_id = client.post("/vehicles", json={
        "plate": "TN 03 EF 9012", "vehicle_type": "truck",
        "capacity_kg": 5000, "insurance_expiry": "2035-12-31",
        "fitness_expiry": "2035-12-31"}).json()["id"]
    driver_id = client.post("/drivers", json={
        "full_name": "Event Driver", "licence_number": "TN0320260001",
        "licence_type": "LMV", "licence_expiry": "2035-12-31",
        "phone": "+91 90000 00000"}).json()["id"]
    shipment_id = client.post("/shipments", json={
        "description": "Event cargo", "weight_kg": 100,
        "origin_id": a, "destination_id": b}).json()["id"]

    closed = client.post(f"/road-edges/{edge}/status",
                         json={"status": "closed"})
    assert closed.status_code == 200
    reopened = client.post(f"/road-edges/{edge}/status",
                           json={"status": "open"})
    assert reopened.status_code == 200

    assigned = client.post("/assignments", json={
        "shipment_id": shipment_id, "vehicle_id": vehicle_id,
        "driver_id": driver_id, "reason": "phase 8B event test"})
    assert assigned.status_code == 200, assigned.text

    kinds = [e["kind"] for e in feed(client).json()]
    assert kinds.count("road.closed") == 1
    assert kinds.count("road.reopened") == 1
    assert kinds.count("assignment.created") == 1
    created = feed(client, kind="assignment.created").json()[0]
    assert created["shipment_id"] == shipment_id
    assert created["vehicle_id"] == vehicle_id

    # filters actually filter
    only_vehicle = feed(client, vehicle_id=vehicle_id).json()
    assert all(e["vehicle_id"] == vehicle_id for e in only_vehicle)
    assert feed(client, kind="road.closed").json()[0]["severity"] == "warning"


def test_events_flow_over_ws(client):
    vehicle_id = make_vehicle(client)
    with client.websocket_connect("/ws/events") as listener:
        client.post(f"/vehicles/{vehicle_id}/position", json={
            "latitude": 13.0, "longitude": 80.0})  # no zone: no event
        client.post("/zones", json={
            "name": "Ws fence", "center_lat": 13.05, "center_lng": 80.05,
            "radius_km": 4, "zone_type": "geofence_alert"})
        client.post(f"/vehicles/{vehicle_id}/position", json={
            "latitude": 13.05, "longitude": 80.05})
        event = listener.receive_json()
    assert event["kind"] == "geofence.enter"
    assert event["details"]["zone"] == "Ws fence"
