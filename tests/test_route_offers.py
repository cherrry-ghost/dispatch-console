"""Phase 14: dispatcher proposes route options, driver picks, risky waits."""
import pytest


def build_world(client):
    """Two parallel routes A->B: short/safe and long/high-risk, a clean
    truck + driver, and one pending shipment."""
    a = client.post("/locations", json={
        "name": "Plant", "location_type": "depot",
        "latitude": 12.0, "longitude": 79.0, "is_depot": True}).json()["id"]
    b = client.post("/locations", json={
        "name": "Port", "location_type": "warehouse",
        "latitude": 12.3, "longitude": 79.3}).json()["id"]
    c = client.post("/locations", json={
        "name": "Hill town", "location_type": "stop",
        "latitude": 12.15, "longitude": 79.5}).json()["id"]
    client.post("/road-edges", json={
        "from_location_id": a, "to_location_id": b,
        "distance_km": 40, "risk_score": 0})
    client.post("/road-edges", json={
        "from_location_id": a, "to_location_id": c,
        "distance_km": 35, "risk_score": 0})
    client.post("/road-edges", json={
        "from_location_id": c, "to_location_id": b,
        "distance_km": 35, "risk_score": 90})
    vehicle_id = client.post("/vehicles", json={
        "plate": "TN 99 ZZ 0001", "vehicle_type": "truck",
        "capacity_kg": 5000, "insurance_expiry": "2035-12-31",
        "fitness_expiry": "2035-12-31"}).json()["id"]
    driver_id = client.post("/drivers", json={
        "full_name": "Offer Driver", "licence_number": "TN9920260001",
        "licence_type": "LMV", "licence_expiry": "2035-12-31",
        "phone": "+91 90000 00001"}).json()["id"]
    shipment_id = client.post("/shipments", json={
        "description": "Cotton bales", "weight_kg": 800,
        "origin_id": a, "destination_id": b}).json()["id"]
    return a, b, c, vehicle_id, driver_id, shipment_id


def test_dispatcher_creates_offer_and_driver_sees_it(client, driver_login):
    _, _, _, vehicle_id, driver_id, shipment_id = build_world(client)
    routes = client.get(
        "/routes",
        params=_od_ids(client, shipment_id)).json()
    assert len(routes["routes"]) >= 2
    labels = [r["label"] for r in routes["routes"]]
    assert "needs_approval" in labels  # the 90-risk hill road
    offer = client.post("/route-offers", json={
        "shipment_id": shipment_id, "vehicle_id": vehicle_id,
        "driver_id": driver_id, "option_indices": [0, 1]})
    assert offer.status_code == 201, offer.text
    body = offer.json()
    assert body["offer_id"] > 0 and len(body["options"]) == 2
    seen = driver_login(driver_id).get(f"/driver/{driver_id}/offers").json()
    assert len(seen) == 1 and seen[0]["id"] == body["offer_id"]
    assert seen[0]["options"] == body["options"]
    audits = [a for a in client.get("/audit").json()
              if a["action"] == "route.offer"]
    assert audits and audits[0]["outcome"] == "allowed"


def _od_ids(client, shipment_id):
    s = next(x for x in client.get("/shipments").json()
             if x["id"] == shipment_id)
    return {"from_id": s["origin_id"], "to_id": s["destination_id"]}


def test_driver_claims_safe_route_job_starts(client, driver_login):
    _, _, _, vehicle_id, driver_id, shipment_id = build_world(client)
    client.post("/route-offers", json={
        "shipment_id": shipment_id, "vehicle_id": vehicle_id,
        "driver_id": driver_id, "option_indices": [0, 1]})
    drv = driver_login(driver_id)
    offers = drv.get(f"/driver/{driver_id}/offers").json()
    safe_index = next(i for i, o in enumerate(offers[0]["options"])
                      if o["label"] == "safe")
    claim = drv.post(f"/driver/{driver_id}/offers/{offers[0]['id']}/claim",
                     json={"option_index": safe_index})
    assert claim.status_code == 200, claim.text
    assert claim.json()["warnings"] == []
    jobs = drv.get(f"/driver/{driver_id}/jobs").json()["jobs"]
    assert len(jobs) == 1
    assert drv.get(f"/driver/{driver_id}/offers").json() == []
    offers_all = client.get("/route-offers").json()
    assert offers_all[0]["status"] == "claimed"
    assert offers_all[0]["assignment_id"] == jobs[0]["assignment_id"]
    audits = [a for a in client.get("/audit").json()
              if a["action"] == "route.claim"]
    assert audits[0]["actor_type"] == "driver"
    assert audits[0]["outcome"] == "allowed"
    assert client.get("/audit/verify").json()["ok"] is True


def test_driver_claims_risky_route_needs_manager(client, manager_client, driver_login):
    _, _, _, vehicle_id, driver_id, shipment_id = build_world(client)
    client.post("/route-offers", json={
        "shipment_id": shipment_id, "vehicle_id": vehicle_id,
        "driver_id": driver_id, "option_indices": [0, 1]})
    drv = driver_login(driver_id)
    offers = drv.get(f"/driver/{driver_id}/offers").json()
    risky_index = next(i for i, o in enumerate(offers[0]["options"])
                       if o["label"] == "needs_approval")
    claim = drv.post(f"/driver/{driver_id}/offers/{offers[0]['id']}/claim",
                     json={"option_index": risky_index})
    assert claim.status_code == 200, claim.text
    body = claim.json()
    assert body["status"] == "pending_approval"
    # job must NOT exist yet
    assert drv.get(f"/driver/{driver_id}/jobs").json()["jobs"] == []
    # manager approves -> assignment appears
    decision = manager_client.post(f"/approvals/{body['approval_id']}/decision",
                            json={"decision": "approved", "note": "ok"})
    assert decision.status_code == 200, decision.text
    jobs = drv.get(f"/driver/{driver_id}/jobs").json()["jobs"]
    assert len(jobs) == 1


def test_claim_twice_is_conflict(client, driver_login):
    _, _, _, vehicle_id, driver_id, shipment_id = build_world(client)
    client.post("/route-offers", json={
        "shipment_id": shipment_id, "vehicle_id": vehicle_id,
        "driver_id": driver_id, "option_indices": [0]})
    drv = driver_login(driver_id)
    offers = drv.get(f"/driver/{driver_id}/offers").json()
    first = drv.post(f"/driver/{driver_id}/offers/{offers[0]['id']}/claim",
                     json={"option_index": 0})
    assert first.status_code == 200
    again = drv.post(f"/driver/{driver_id}/offers/{offers[0]['id']}/claim",
                     json={"option_index": 0})
    assert again.status_code == 409


def test_other_driver_cannot_claim(client, driver_login):
    _, _, _, vehicle_id, driver_id, shipment_id = build_world(client)
    client.post("/route-offers", json={
        "shipment_id": shipment_id, "vehicle_id": vehicle_id,
        "driver_id": driver_id, "option_indices": [0]})
    stranger = client.post("/drivers", json={
        "full_name": "Stranger S", "licence_number": "TN9920260002",
        "licence_type": "LMV", "licence_expiry": "2035-12-31",
        "phone": "+91 90000 00002"}).json()["id"]
    res = driver_login(stranger).post(
        f"/driver/{stranger}/offers/{shipment_id}/claim",
        json={"option_index": 0})
    assert res.status_code == 404  # offer is not visible to this driver


def test_claim_blocked_by_guardrails(client, driver_login):
    """Guardrails run at claim time: an over-capacity combo never saves."""
    a, b, c, vehicle_id, driver_id, _ = build_world(client)
    shipment_id = client.post("/shipments", json={
        "description": "Way too heavy", "weight_kg": 9000,
        "origin_id": a, "destination_id": b}).json()["id"]
    client.post("/route-offers", json={
        "shipment_id": shipment_id, "vehicle_id": vehicle_id,
        "driver_id": driver_id, "option_indices": [0]})
    drv = driver_login(driver_id)
    offers = drv.get(f"/driver/{driver_id}/offers").json()
    claim = drv.post(f"/driver/{driver_id}/offers/{offers[0]['id']}/claim",
                     json={"option_index": 0})
    assert claim.status_code == 422
    assert "blocked by guardrails" in claim.json()["detail"]["message"]
    assert drv.get(f"/driver/{driver_id}/jobs").json()["jobs"] == []
    audits = [a for a in client.get("/audit").json()
              if a["action"] == "route.claim"]
    assert audits[0]["outcome"] == "blocked"
