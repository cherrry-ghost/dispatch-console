"""Phase 8D: customer notifications and driver-side incident reporting."""
import pytest


def build_world(client, customer=None):
    """Depot -> stop, legal truck + driver, assigned shipment with optional
    customer contact. Returns (driver_id, assignment_id, shipment_id)."""
    a = client.post("/locations", json={
        "name": "Depot 8D", "location_type": "depot",
        "latitude": 14.0, "longitude": 79.5, "is_depot": True}).json()["id"]
    b = client.post("/locations", json={
        "name": "Stop 8D", "location_type": "warehouse",
        "latitude": 14.1, "longitude": 79.6}).json()["id"]
    client.post("/road-edges", json={
        "from_location_id": a, "to_location_id": b, "distance_km": 22})
    vehicle_id = client.post("/vehicles", json={
        "plate": "TN 09 ZZ 1010", "vehicle_type": "truck",
        "capacity_kg": 6000, "insurance_expiry": "2035-12-31",
        "fitness_expiry": "2035-12-31"}).json()["id"]
    driver_id = client.post("/drivers", json={
        "full_name": "Notify Driver", "licence_number": "TN0920260555",
        "licence_type": "LMV", "licence_expiry": "2035-12-31",
        "phone": "+91 98888 77777"}).json()["id"]
    shipment = client.post("/shipments", json={
        "description": "Fragile cargo", "weight_kg": 500,
        "origin_id": a, "destination_id": b,
        **({"customer_name": customer["name"], "customer_phone": customer["phone"]}
           if customer else {})}).json()
    assigned = client.post("/assignments", json={
        "shipment_id": shipment["id"], "vehicle_id": vehicle_id,
        "driver_id": driver_id, "reason": "8D test"}).json()
    jobs = client.get(f"/driver/{driver_id}/jobs").json()["jobs"]
    return driver_id, jobs[0]["assignment_id"], shipment["id"]


def test_customer_contact_stored_with_shipment(client):
    _, _, shipment_id = build_world(client, customer={
        "name": "Asha Traders", "phone": "+91 90000 11111"})
    shipment = next(s for s in client.get("/shipments").json()
                    if s["id"] == shipment_id)
    assert shipment["customer_name"] == "Asha Traders"
    assert shipment["customer_phone"] == "+91 90000 11111"


def test_assignment_and_delivery_notify_customer(client, driver_login):
    driver_id, assignment_id, shipment_id = build_world(client, customer={
        "name": "Asha Traders", "phone": "+91 90000 11111"})
    driver = driver_login(driver_id)

    notes = client.get(f"/notifications?shipment_id={shipment_id}").json()
    assert [n["kind"] for n in notes] == ["assigned"]
    assert "on the way" in notes[0]["message"]
    assert notes[0]["recipient"] == "Asha Traders"

    result = driver.post(
        f"/driver/{driver_id}/jobs/{assignment_id}/deliver")
    assert result.status_code == 200, result.text

    notes = client.get(f"/notifications?shipment_id={shipment_id}").json()
    assert [n["kind"] for n in notes] == ["delivered", "assigned"]
    assert "delivered" in notes[0]["message"]

    # the pipeline carried them live as well
    events = client.get("/events?kind=notification.sent&limit=10").json()
    assert {e["details"]["kind"] for e in events} >= {"assigned", "delivered"}


def test_no_customer_contact_means_no_notification(client, driver_login):
    driver_id, assignment_id, shipment_id = build_world(client)  # no customer
    result = driver_login(driver_id).post(
        f"/driver/{driver_id}/jobs/{assignment_id}/deliver")
    assert result.status_code == 200  # delivery still works
    assert client.get("/notifications").json() == []


def test_driver_incident_report_notifies_customer_and_audits(client, driver_login):
    driver_id, assignment_id, shipment_id = build_world(client, customer={
        "name": "Asha Traders", "phone": "+91 90000 11111"})
    result = driver_login(driver_id).post(
        f"/driver/{driver_id}/jobs/{assignment_id}/incident", json={
            "description": "Tree fallen across the road near the bridge",
            "incident_type": "road_blocked"})
    assert result.status_code == 200, result.text
    incident_id = result.json()["incident_id"]

    inc = client.get(f"/incidents/{incident_id}").json()
    assert inc["status"] == "reported"
    assert inc["reported_by"] == f"driver:{driver_id}"

    notes = client.get(f"/notifications?shipment_id={shipment_id}").json()
    kinds = [n["kind"] for n in notes]
    assert "incident" in kinds
    incident_note = next(n for n in notes if n["kind"] == "incident")
    assert "handling it" in incident_note["message"]

    audits = client.get("/audit").json()
    mine = [a for a in audits if a["action"] == "incident.reported"
            and a["actor_type"] == "driver"]
    assert len(mine) == 1 and mine[0]["actor_id"] == str(driver_id)
    events = client.get("/events?kind=incident.reported&limit=5").json()
    assert any(e["details"]["incident_id"] == incident_id for e in events)
    assert client.get("/audit/verify").json()["ok"] is True


def test_driver_incident_rejections(client, driver_login):
    driver_id, assignment_id, _ = build_world(client)
    driver = driver_login(driver_id)
    stranger = client.post("/drivers", json={
        "full_name": "X Driver", "licence_number": "TN0920260666",
        "licence_type": "LMV", "licence_expiry": "2035-12-31",
        "phone": "+91 90000 22222"}).json()["id"]
    # token/path mismatch and missing driver
    assert driver.post(f"/driver/{stranger}/jobs/{assignment_id}/incident",
                       json={"description": "s", "incident_type": "other"}
                       ).status_code == 401
    assert client.post(f"/driver/{stranger}/jobs/{assignment_id}/incident",
                       json={"description": "s", "incident_type": "other"}
                       ).status_code == 401
    empty = driver.post(
        f"/driver/{driver_id}/jobs/{assignment_id}/incident",
        json={"description": "   ", "incident_type": "other"})
    assert empty.status_code == 422
