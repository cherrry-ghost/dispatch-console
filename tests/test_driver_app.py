"""Phase 8C: driver app - jobs list, proof-of-delivery, PWA serving."""
import hashlib
import os

import pytest

EVIDENCE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "evidence")


def build_world(client):
    """One depot, one stop, a legal truck + driver, one assigned shipment."""
    a = client.post("/locations", json={
        "name": "Home depot", "location_type": "depot",
        "latitude": 12.0, "longitude": 79.0, "is_depot": True}).json()["id"]
    b = client.post("/locations", json={
        "name": "Mall stop", "location_type": "warehouse",
        "latitude": 12.1, "longitude": 79.1}).json()["id"]
    client.post("/road-edges", json={
        "from_location_id": a, "to_location_id": b, "distance_km": 25})
    vehicle_id = client.post("/vehicles", json={
        "plate": "TN 08 XY 4321", "vehicle_type": "truck",
        "capacity_kg": 5000, "insurance_expiry": "2035-12-31",
        "fitness_expiry": "2035-12-31"}).json()["id"]
    driver_id = client.post("/drivers", json={
        "full_name": "PWA Driver", "licence_number": "TN0820260777",
        "licence_type": "LMV", "licence_expiry": "2035-12-31",
        "phone": "+91 91234 56789"}).json()["id"]
    shipment_id = client.post("/shipments", json={
        "description": "Pallet of tiles", "weight_kg": 900,
        "origin_id": a, "destination_id": b}).json()["id"]
    assigned = client.post("/assignments", json={
        "shipment_id": shipment_id, "vehicle_id": vehicle_id,
        "driver_id": driver_id, "reason": "8C test"}).json()
    assert "id" in assigned or assigned.get("status") == "assigned", assigned
    assignment_id = assigned.get("id") or assigned.get("assignment_id")
    if assignment_id is None:  # some flows return the row dict directly
        jobs = client.get(f"/driver/{driver_id}/jobs").json()["jobs"]
        assignment_id = jobs[0]["assignment_id"]
    return driver_id, assignment_id, shipment_id


def test_jobs_lists_only_that_driver(client):
    driver_id, assignment_id, shipment_id = build_world(client)
    data = client.get(f"/driver/{driver_id}/jobs").json()
    assert data["driver"]["id"] == driver_id
    assert data["driver"]["licence_expiry"] == "2035-12-31"
    assert len(data["jobs"]) == 1
    assert data["completed"] == []
    assert data["pending_pool"] == 0  # the only shipment was just assigned
    job = data["jobs"][0]
    assert job["assignment_id"] == assignment_id
    assert job["shipment_id"] == shipment_id
    assert job["description"] == "Pallet of tiles"
    assert job["origin_name"] == "Home depot"
    assert job["destination_name"] == "Mall stop"
    # a different driver sees nothing
    other = client.post("/drivers", json={
        "full_name": "Other Driver", "licence_number": "TN0820260888",
        "licence_type": "LMV", "licence_expiry": "2035-12-31",
        "phone": "+91 91234 50000"}).json()["id"]
    assert client.get(f"/driver/{other}/jobs").json()["jobs"] == []
    assert client.get("/driver/424242/jobs").status_code == 404


def test_deliver_completes_job_without_photo(client, driver_login):
    driver_id, assignment_id, shipment_id = build_world(client)
    driver = driver_login(driver_id)
    result = driver.post(
        f"/driver/{driver_id}/jobs/{assignment_id}/deliver",
        data={"note": "handed to storekeeper"})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["proof_recorded"] is False
    assert body["shipment_id"] == shipment_id

    # shipment completed, assignment closed
    shipments = client.get("/shipments").json()
    done = next(s for s in shipments if s["id"] == shipment_id)
    assert done["status"] == "completed" and done["delivered_at"] is not None
    data = client.get(f"/driver/{driver_id}/jobs").json()
    assert data["jobs"] == []
    assert len(data["completed"]) == 1
    assert data["completed"][0]["assignment_id"] == assignment_id
    assert data["completed"][0]["delivery_note"] == "handed to storekeeper"

    # pipeline event + audit under the driver's own name
    events = client.get("/events?kind=assignment.completed").json()
    assert len(events) == 1 and events[0]["shipment_id"] == shipment_id
    audits = [a for a in client.get("/audit").json()
              if a["action"] == "delivery.confirmed"]
    assert len(audits) == 1
    assert audits[0]["actor_type"] == "driver"
    assert audits[0]["actor_id"] == str(driver_id)
    assert audits[0]["details"]["photo_optional"] is True
    assert client.get("/audit/verify").json()["ok"] is True


def test_deliver_with_photo_records_hash(client, driver_login):
    driver_id, assignment_id, shipment_id = build_world(client)
    driver = driver_login(driver_id)
    png = b"\x89PNG fake image bytes for hashing"
    result = driver.post(
        f"/driver/{driver_id}/jobs/{assignment_id}/deliver",
        data={"note": "proof attached"},
        files={"photo": (" doorstep.png", png, "image/png")})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["proof_recorded"] is True
    digest = hashlib.sha256(png).hexdigest()
    audits = [a for a in client.get("/audit").json()
              if a["action"] == "delivery.confirmed"]
    assert audits[0]["details"]["proof_sha256"] == digest
    stored = [f for f in os.listdir(EVIDENCE_DIR)
              if f.startswith(f"delivery_{assignment_id}_")
              and f.endswith(".png") and digest[:12] in f]
    assert len(stored) == 1
    with open(os.path.join(EVIDENCE_DIR, stored[0]), "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == digest
    os.remove(os.path.join(EVIDENCE_DIR, stored[0]))  # leave no test litter


def test_deliver_rejections(client, driver_login):
    driver_id, assignment_id, _ = build_world(client)
    driver = driver_login(driver_id)
    # console users may not deliver; a driver token is required
    assert client.post(
        f"/driver/{driver_id}/jobs/{assignment_id}/deliver"
        ).status_code == 401
    # wrong driver: stranger's token does not match the path
    stranger = client.post("/drivers", json={
        "full_name": "Stranger", "licence_number": "TN0820260999",
        "licence_type": "LMV", "licence_expiry": "2035-12-31",
        "phone": "+91 91234 90000"}).json()["id"]
    assert driver.post(
        f"/driver/{stranger}/jobs/{assignment_id}/deliver").status_code == 401
    assert driver.post(
        f"/driver/{driver_id}/jobs/987654/deliver").status_code == 404
    # double delivery
    assert driver.post(
        f"/driver/{driver_id}/jobs/{assignment_id}/deliver").status_code == 200
    assert driver.post(
        f"/driver/{driver_id}/jobs/{assignment_id}/deliver").status_code == 409


def test_pwa_files_are_served(client):
    shell = client.get("/driver-app/")
    assert shell.status_code == 200
    assert "Driver App" in shell.text
    for path, marker in [("app.js", "flushQueue"),
                         ("manifest.webmanifest", '"short_name"'),
                         ("sw.js", "driver-shell-"),
                         ("icon.svg", "<svg")]:
        res = client.get(f"/driver-app/{path}")
        assert res.status_code == 200, path
        assert marker in res.text, path
