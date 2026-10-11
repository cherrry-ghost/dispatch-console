import os

import psycopg
import pytest

from audit import verify_chain
from trust import eta_trust, planner_trust
from delay_model import MODEL_PATHS


@pytest.fixture(autouse=True)
def clean_model_files():
    for path in MODEL_PATHS:
        if os.path.exists(path):
            os.remove(path)
    yield
    for path in MODEL_PATHS:
        if os.path.exists(path):
            os.remove(path)


def run_datagen(client, body=None):
    return client.post("/datagen", json=body or {})


def test_formula_boundaries():
    assert eta_trust(None, None)[0] == 0
    score, factors = eta_trust(15, 1.0, uncertainty_width=20, samples=100)
    assert score == 100
    assert factors["accuracy_bonus"] == 15.0
    assert factors["coverage_bonus"] == 30.0
    score_bad, _ = eta_trust(120, 0.1, uncertainty_width=200,
                             drift_detected=True, samples=5)
    assert score_bad == 18  # 50 + 3 coverage - 10 wide - 5 tiny - 20 drift
    assert planner_trust(0, 0, "offline-estimate", 0)[0] == 20
    assert planner_trust(50, 5, "road-network", 8)[0] == 75


def test_health_untrained_reports_zero_trust(client):
    r = client.get("/model/health")
    assert r.status_code == 200
    data = r.json()
    assert data["trained"] is False
    assert data["trust_score"]["score"] == 0
    assert "POST /model/train" in data["how_to_train"]


def test_health_after_training_reports_drift_and_trust(client):
    run_datagen(client, {"scale": "medium", "seed": 4})
    assert client.post("/model/train", json={}).status_code == 200

    r = client.get("/model/health")
    data = r.json()
    assert data["trained"] is True
    assert data["model_version"] == 2
    assert data["samples"] >= 40
    assert data["holdout"]["mae_minutes"] < 45
    assert 0.0 <= data["holdout"]["interval_coverage"] <= 1.0
    assert data["drift"]["status"] in ("ok", "unknown", "drift_detected")
    assert 0 <= data["trust_score"]["score"] <= 100
    assert data["trust_score"]["score"] >= 60  # a healthy honest model


def test_health_is_audited(client):
    r = client.get("/model/health")
    assert r.status_code == 200
    rows = [x for x in client.get("/audit?limit=20").json()
            if x["action"] == "model.health"]
    assert len(rows) == 1
    assert rows[0]["actor_type"] == "system"
    assert rows[0]["details"]["trained"] is False


def test_planted_drift_is_detected_and_lowers_trust(client):
    run_datagen(client, {"scale": "medium", "seed": 4})
    assert client.post("/model/train", json={}).status_code == 200
    before = client.get("/model/health").json()

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rows = conn.execute(
            "SELECT id FROM shipments WHERE status = 'completed' "
            "AND delay_minutes IS NOT NULL "
            "ORDER BY delivered_at DESC NULLS LAST LIMIT 30"
        ).fetchall()
        ids = [r[0] for r in rows]
        conn.execute(
            "UPDATE shipments SET delay_minutes = delay_minutes + 2500 "
            "WHERE id = ANY(%s)", (ids,))

    after = client.get("/model/health").json()
    assert after["drift"]["status"] == "drift_detected"
    assert after["drift"]["ratio"] > 1.5
    assert after["trust_score"]["score"] < before["trust_score"]["score"]
    assert after["trust_score"]["factors"]["drift_penalty"] == -20


def test_predict_carries_trust_score(client):
    run_datagen(client, {"scale": "medium", "seed": 4})
    assert client.post("/model/train", json={}).status_code == 200
    pending = next(s for s in client.get("/shipments").json()
                   if s["status"] == "pending")

    p1 = client.post("/predict/eta", json={"shipment_id": pending["id"]})
    p2 = client.post("/predict/eta", json={"shipment_id": pending["id"]})
    assert p1.status_code == 200
    trust = p1.json()["trust_score"]
    assert 0 <= trust["score"] <= 100
    assert trust["score"] >= 60
    assert "accuracy_bonus" in trust["factors"]
    assert p2.json()["trust_score"] == trust  # deterministic

    logged = [x for x in client.get("/audit?limit=30").json()
              if x["action"] == "eta.predicted"]
    assert logged and logged[0]["details"]["trust_score"] == trust["score"]
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        assert verify_chain(conn)["ok"] is True


def test_planner_declares_trust_and_penalises_offline_mode(client):
    # no roads yet: the planner must warn through a lower score
    origin = client.post("/locations", json={
        "name": "A", "location_type": "depot",
        "latitude": 11.0, "longitude": 78.0, "is_depot": True}).json()["id"]
    dest = client.post("/locations", json={
        "name": "B", "location_type": "warehouse",
        "latitude": 11.1, "longitude": 78.1}).json()["id"]
    client.post("/vehicles", json={
        "plate": "TN61AA0001", "vehicle_type": "truck",
        "capacity_kg": 5000, "insurance_expiry": "2035-12-31",
        "fitness_expiry": "2035-12-31"})
    client.post("/shipments", json={
        "description": "Load", "weight_kg": 1000,
        "origin_id": origin, "destination_id": dest})

    offline = client.post("/planner/propose", json={}).json()
    assert offline["trust_score"]["score"] == 45  # 70 - 25 offline travel
    assert offline["trust_score"]["factors"]["offline_travel_penalty"] == -25

    client.post("/road-edges", json={
        "from_location_id": origin, "to_location_id": dest,
        "distance_km": 30})
    online = client.post("/planner/propose", json={}).json()
    assert online["trust_score"]["score"] > offline["trust_score"]["score"]
    assert "offline_travel_penalty" not in online["trust_score"]["factors"]
