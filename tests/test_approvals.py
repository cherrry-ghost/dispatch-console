import os

import psycopg

from audit import verify_chain
from test_assignments import make_driver, make_shipment, make_vehicle


def setup_world(client, weight):
    vehicle = make_vehicle(client)
    driver = make_driver(client, "L-APPROVAL-1")
    shipment = make_shipment(client, weight=weight)
    return {"shipment_id": shipment, "vehicle_id": vehicle,
            "driver_id": driver, "reason": "test"}


def actions(client):
    return [r["action"] for r in client.get("/audit?limit=50").json()]


def test_clean_assignment_is_immediate(client, manager_client):
    req = setup_world(client, weight=2000)
    response = client.post("/assignments", json=req)
    assert response.status_code == 200
    assert client.get("/approvals").json() == []


def test_warning_goes_to_pending_approval(client, manager_client):
    req = setup_world(client, weight=4800)  # 96% of 5000 kg -> near_capacity warn
    response = client.post("/assignments", json=req)
    assert response.status_code == 202
    assert response.json()["status"] == "pending_approval"
    assert client.get("/assignments").json() == []  # nothing applied yet
    pending = client.get("/approvals?status=pending").json()
    assert len(pending) == 1
    assert pending[0]["warnings"][0]["rule"] == "near_capacity"
    assert "assignment.request_approval" in actions(client)


def test_approve_applies_assignment(client, manager_client):
    req = setup_world(client, weight=4800)
    approval_id = client.post("/assignments", json=req).json()["approval_id"]
    response = manager_client.post(
        f"/approvals/{approval_id}/decision",
        json={"decision": "approved", "note": "ok"},
    )
    assert response.status_code == 200
    assignments = client.get("/assignments").json()
    assert len(assignments) == 1
    assert assignments[0]["shipment_id"] == req["shipment_id"]
    assert "approval.approved" in actions(client)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        assert verify_chain(conn)["ok"] is True


def test_reject_does_not_apply(client, manager_client):
    req = setup_world(client, weight=4800)
    approval_id = client.post("/assignments", json=req).json()["approval_id"]
    response = manager_client.post(
        f"/approvals/{approval_id}/decision",
        json={"decision": "rejected", "note": "too risky"},
    )
    assert response.status_code == 200
    assert client.get("/assignments").json() == []
    assert client.get("/approvals").json()[0]["status"] == "rejected"
    assert "approval.rejected" in actions(client)


def test_cannot_decide_twice(client, manager_client):
    req = setup_world(client, weight=4800)
    approval_id = client.post("/assignments", json=req).json()["approval_id"]
    body = {"decision": "approved", "note": ""}
    assert manager_client.post(f"/approvals/{approval_id}/decision", json=body).status_code == 200
    assert manager_client.post(f"/approvals/{approval_id}/decision", json=body).status_code == 409


def test_unknown_approval_is_404(client, manager_client):
    body = {"decision": "approved", "note": ""}
    assert manager_client.post("/approvals/999/decision", json=body).status_code == 404


def test_hard_block_is_never_sent_to_approval(client, manager_client):
    req = setup_world(client, weight=9000)  # over capacity -> block
    response = client.post("/assignments", json=req)
    assert response.status_code == 422
    assert client.get("/approvals").json() == []