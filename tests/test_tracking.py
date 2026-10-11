"""Phase 8A: live vehicle tracking - REST ingest, WebSocket channels,
zone alerts and the audit trail around them."""
import pytest

import main


@pytest.fixture(autouse=True)
def clean_tracking_state():
    """Reset in-memory tracking state so tests are independent."""
    main._in_zone_state.clear()
    main.live.viewers.clear()
    yield
    main._in_zone_state.clear()
    main.live.viewers.clear()


def make_vehicle(client):
    response = client.post("/vehicles", json={
        "plate": "TN 01 AB 1234", "vehicle_type": "truck",
        "capacity_kg": 5000,
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_position_rest_roundtrip(client):
    vehicle_id = make_vehicle(client)
    response = client.post(f"/vehicles/{vehicle_id}/position", json={
        "latitude": 13.0827, "longitude": 80.2707, "speed_kmh": 42,
    })
    assert response.status_code == 200, response.text
    assert response.json() == {
        "vehicle_id": vehicle_id, "zone_alert": None, "recorded": True}

    latest = client.get(f"/vehicles/{vehicle_id}/position")
    assert latest.status_code == 200
    row = latest.json()
    assert abs(row["latitude"] - 13.0827) < 1e-9
    assert abs(row["longitude"] - 80.2707) < 1e-9
    assert row["speed_kmh"] == 42
    assert row["source"] == "gps_device"

    fleet = client.get("/vehicles/positions")
    assert fleet.status_code == 200
    entries = [p for p in fleet.json() if p["vehicle_id"] == vehicle_id]
    assert len(entries) == 1


def test_latest_position_only_keeps_newest(client):
    vehicle_id = make_vehicle(client)
    for lat in (13.0, 13.1, 13.2):
        response = client.post(f"/vehicles/{vehicle_id}/position", json={
            "latitude": lat, "longitude": 80.0})
        assert response.status_code == 200
    row = client.get(f"/vehicles/{vehicle_id}/position").json()
    assert abs(row["latitude"] - 13.2) < 1e-9
    fleet = client.get("/vehicles/positions").json()
    mine = [p for p in fleet if p["vehicle_id"] == vehicle_id]
    assert len(mine) == 1 and abs(mine[0]["latitude"] - 13.2) < 1e-9


def test_position_validation(client):
    vehicle_id = make_vehicle(client)
    bad_lat = client.post(f"/vehicles/{vehicle_id}/position", json={
        "latitude": 999, "longitude": 80.0})
    assert bad_lat.status_code == 422
    bad_speed = client.post(f"/vehicles/{vehicle_id}/position", json={
        "latitude": 13.0, "longitude": 80.0, "speed_kmh": 9000})
    assert bad_speed.status_code == 422
    missing = client.get("/vehicles/999999/position")
    assert missing.status_code == 404
    unknown = client.post("/vehicles/999999/position", json={
        "latitude": 13.0, "longitude": 80.0})
    assert unknown.status_code == 404


def test_ws_vehicle_ingest_and_audit(client):
    vehicle_id = make_vehicle(client)
    with client.websocket_connect(f"/ws/vehicle/{vehicle_id}") as ws:
        ws.send_json({"latitude": 13.05, "longitude": 80.1,
                      "speed_kmh": 30, "source": "driver_app"})
        ack = ws.receive_json()
        assert ack == {"status": "ok", "zone_alert": None}
        ws.send_json({"latitude": 13.06, "longitude": 80.11})
        ack2 = ws.receive_json()
        assert ack2["status"] == "ok"

    row = client.get(f"/vehicles/{vehicle_id}/position").json()
    assert abs(row["latitude"] - 13.06) < 1e-9
    assert row["source"] == "driver_app"

    with client.websocket_connect(f"/ws/vehicle/{vehicle_id}") as ws:
        ws.send_json({"longitude": 80.2})  # missing latitude
        assert ws.receive_json()["status"] == "error"

    actions = [a["action"] for a in client.get("/audit").json()]
    assert actions.count("tracking.started") == 2
    assert actions.count("tracking.stopped") == 2
    assert client.get("/audit/verify").json()["ok"] is True


def test_ws_live_broadcast(client):
    vehicle_id = make_vehicle(client)
    with client.websocket_connect("/ws/live") as viewer:
        response = client.post(f"/vehicles/{vehicle_id}/position", json={
            "latitude": 13.01, "longitude": 80.02, "speed_kmh": 55})
        assert response.status_code == 200
        msg = viewer.receive_json()
    assert msg == {
        "type": "position", "vehicle_id": vehicle_id,
        "latitude": 13.01, "longitude": 80.02, "speed_kmh": 55,
        "source": "gps_device", "zone_alert": None,
    }


def test_ws_vehicle_not_found(client):
    with client.websocket_connect("/ws/vehicle/424242") as ws:
        msg = ws.receive_json()
    assert msg["status"] == "error"
    actions = [a["action"] for a in client.get("/audit").json()]
    assert "tracking.started" not in actions


def test_zone_alert_on_enter_and_reenter(client):
    vehicle_id = make_vehicle(client)
    zone = client.post("/zones", json={
        "name": "No-fly market", "center_lat": 13.10, "center_lng": 80.20,
        "radius_km": 5.0})
    assert zone.status_code == 200, zone.text

    inside = client.post(f"/vehicles/{vehicle_id}/position", json={
        "latitude": 13.1005, "longitude": 80.2005})
    assert inside.json()["zone_alert"] == {
        "zone_id": zone.json()["id"], "zone_name": "No-fly market",
        "radius_km": 5.0}

    outside = client.post(f"/vehicles/{vehicle_id}/position", json={
        "latitude": 14.5, "longitude": 81.5})
    assert outside.json()["zone_alert"] is None

    again = client.post(f"/vehicles/{vehicle_id}/position", json={
        "latitude": 13.11, "longitude": 80.21})
    assert again.json()["zone_alert"] is not None

    audits = client.get("/audit").json()
    breaches = [a for a in audits if a["action"] == "vehicle.in_zone"]
    assert len(breaches) == 2  # fires on transitions, not every ping
    assert breaches[0]["actor_type"] == "system"
    assert breaches[0]["outcome"] == "warning"
    assert breaches[0]["details"]["zone"] == "No-fly market"
    assert client.get("/audit/verify").json()["ok"] is True
