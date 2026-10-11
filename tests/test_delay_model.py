import os

import psycopg

from audit import verify_chain
from delay_model import (
    MODEL_PATH, synthetic_truth, route_features, travel_minutes,
)


def teardown_function():
    if os.path.exists(MODEL_PATH):
        os.remove(MODEL_PATH)  # tests must not leave a trained model behind


def test_history_shipments_are_generated_with_true_delays(client):
    assert client.post("/datagen", json={"scale": "small", "seed": 7}
                       ).status_code == 200
    completed = [s for s in client.get("/shipments").json()
                 if s["status"] == "completed"]
    assert len(completed) == 20
    for s in completed:
        assert s["delay_minutes"] is not None and s["delay_minutes"] >= 0
        assert s["eta_minutes"] is not None
        assert s["delivered_at"] is not None


def test_synthetic_truth_is_deterministic(client):
    from random import Random
    features = {"distance_km": 120.0, "max_risk": 40, "avg_risk": 10.0,
                "is_hazardous": 1, "weight_kg": 3000, "legs": 3}
    a = synthetic_truth(features, Random(5))
    b = synthetic_truth(features, Random(5))
    assert a == b
    assert travel_minutes(features) == 180  # 120 km at 40 km/h


def test_training_needs_history(client):
    r = client.post("/model/train", json={})
    assert r.status_code == 409
    assert "history" in r.json()["detail"]


def test_predict_before_training_is_409(client):
    client.post("/datagen", json={"scale": "small", "seed": 1})
    shipment_id = client.get("/shipments").json()[0]["id"]
    r = client.post("/predict/eta", json={"shipment_id": shipment_id})
    assert r.status_code == 409
    assert "train" in r.json()["detail"]


def test_full_train_and_predict_flow(client):
    client.post("/datagen", json={"scale": "medium", "seed": 4})

    t = client.post("/model/train", json={"holdout_fraction": 0.25})
    assert t.status_code == 200
    data = t.json()
    assert data["samples"] >= 40
    assert data["holdout_samples"] >= 5
    assert data["model"] == "xgboost"
    assert data["mae_minutes_holdout"] < 45  # truth noise sigma is 20 min
    assert os.path.exists(MODEL_PATH)

    pending = next(s for s in client.get("/shipments").json()
                   if s["status"] == "pending")
    p1 = client.post("/predict/eta", json={"shipment_id": pending["id"]})
    assert p1.status_code == 200
    eta = p1.json()
    assert eta["eta_minutes"] >= eta["travel_minutes"]
    assert eta["predicted_delay_minutes"] >= 0
    assert eta["features_used"]["avg_risk"] is not None
    assert eta["model"]["version"] == 1

    # deterministic: same shipment, same answer
    p2 = client.post("/predict/eta", json={"shipment_id": pending["id"]})
    assert p2.json()["eta_minutes"] == eta["eta_minutes"]

    # the model proposed and nothing else: shipment untouched
    after = next(s for s in client.get("/shipments").json()
                 if s["id"] == pending["id"])
    assert after["status"] == "pending"
    assert after["delay_minutes"] is None

    logged = [row for row in client.get("/audit?limit=50").json()
              if row["action"] == "eta.predicted"]
    assert logged and logged[0]["actor_type"] == "ai_delay_model"
    assert logged[0]["details"]["model"] == "xgboost"
    trained = [row for row in client.get("/audit?limit=50").json()
               if row["action"] == "model.trained"]
    assert trained and trained[0]["details"]["mae_minutes_holdout"] < 45
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        assert verify_chain(conn)["ok"] is True


def test_predict_unknown_shipment_is_404(client):
    client.post("/datagen", json={"scale": "small", "seed": 2})
    client.post("/model/train", json={})
    r = client.post("/predict/eta", json={"shipment_id": 9999})
    assert r.status_code == 404


def test_route_features_shape():
    route = {"path": [1, 2, 3], "distance_km": 30.0, "max_risk": 20,
             "avg_risk": 10.0}
    shipment = {"is_hazardous": True, "weight_kg": 2000}
    f = route_features(route, shipment)
    assert f == {"distance_km": 30.0, "max_risk": 20, "avg_risk": 10.0,
                 "is_hazardous": 1, "weight_kg": 2000, "legs": 2}
