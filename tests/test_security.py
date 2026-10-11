"""Phase 10: security - login, roles, tokens, driver PIN, audit actor."""
import security


def test_login_ok_and_wrong_password(client, anon_client):
    result = anon_client.post("/login", json={
        "username": "dispatcher", "password": "dispatch123"})
    assert result.status_code == 200
    body = result.json()
    assert body["role"] == "dispatcher" and body["token"]
    assert anon_client.post("/login", json={
        "username": "dispatcher", "password": "wrong"}).status_code == 401
    assert anon_client.post("/login", json={
        "username": "ghost", "password": "dispatch123"}).status_code == 401


def test_protected_endpoints_reject_anonymous(client, anon_client):
    assert anon_client.post("/vehicles", json={}).status_code == 401
    assert anon_client.post("/shipments", json={}).status_code == 401
    assert anon_client.post("/assignments", json={}).status_code == 401
    assert anon_client.post("/copilot/ask", json={
        "question": "hi"}).status_code == 401
    # read-only endpoints stay open for dashboards
    assert anon_client.get("/vehicles").status_code == 200
    assert anon_client.get("/audit").status_code == 200


def test_tampered_and_garbage_tokens_rejected(client, anon_client):
    good = security.make_token("dispatcher", "dispatcher")
    assert anon_client.post("/vehicles", json={},
                            headers={"Authorization": f"Bearer {good}x"}
                            ).status_code == 401
    assert anon_client.post("/vehicles", json={},
                            headers={"Authorization": "Basic abc"}
                            ).status_code == 401
    # wrong secret -> signature mismatch
    old = security.SECRET
    security.SECRET = "other-secret"
    forged = security.make_token("dispatcher", "dispatcher")
    security.SECRET = old
    assert anon_client.post("/vehicles", json={},
                            headers={"Authorization": f"Bearer {forged}"}
                            ).status_code == 401


def test_roles_dispatcher_vs_manager(client, anon_client):
    # dispatcher may create a vehicle but not decide approvals...
    vehicle = client.post("/vehicles", json={
        "plate": "TN 01 SC 0001", "vehicle_type": "truck",
        "capacity_kg": 5000, "insurance_expiry": "2035-12-31",
        "fitness_expiry": "2035-12-31"})
    assert vehicle.status_code == 200
    assert client.post("/approvals/1/decision", json={
        "decision": "approved"}).status_code == 403
    # ...and the manager may decide (404: no such approval, but NOT 403)
    assert manager_decides(anon_client) == 404


def manager_decides(anon_client):
    token = anon_client.post("/login", json={
        "username": "manager", "password": "manager123"}).json()
    response = anon_client.post(
        "/approvals/1/decision", json={"decision": "approved"},
        headers={"Authorization": f"Bearer {token['token']}"})
    return response.status_code


def test_datagen_needs_admin_or_dispatcher_but_not_manager(client,
                                                           manager_client):
    assert manager_client.post("/datagen", json={
        "seed": 1, "n_locations": 2, "n_vehicles": 1, "n_drivers": 1,
        "n_shipments": 1}).status_code == 403


def test_audit_rows_carry_the_real_username(client, anon_client):
    client.post("/vehicles", json={
        "plate": "TN 01 SC 0002", "vehicle_type": "truck",
        "capacity_kg": 5000, "insurance_expiry": "2035-12-31",
        "fitness_expiry": "2035-12-31"})
    entry = next(a for a in client.get("/audit").json()
                 if a["action"] == "vehicle.create")
    assert entry["actor_id"] == "dispatcher"


def test_driver_login_pin_flow(client, driver_login):
    driver_id = client.post("/drivers", json={
        "full_name": "PIN Driver", "licence_number": "TN1020260001",
        "licence_expiry": "2035-12-31"}).json()["id"]
    driver = driver_login(driver_id)  # default PIN 1234
    assert driver.get(f"/driver/{driver_id}/jobs").status_code == 200
    # wrong PIN refused
    bad = client.post("/driver/login",
                      json={"driver_id": driver_id, "pin": "9999"})
    assert bad.status_code == 401
    # driver token cannot use console endpoints
    assert driver.post("/vehicles", json={}).status_code == 403


def test_health_and_metrics(client, anon_client):
    health = anon_client.get("/health").json()
    assert health["status"] == "ok"
    assert health["database"] == "ok"
    assert health["delay_model"] in ("trained", "untrained")
    metrics = anon_client.get("/metrics").text
    assert "dispatch_requests_total" in metrics
    assert "dispatch_audit_entries" in metrics
