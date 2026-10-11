import os

import psycopg
import pytest

from audit import verify_chain
from anomalies import scan
from datetime import timedelta
from delay_model import MODEL_PATHS


@pytest.fixture(autouse=True)
def no_stale_model():
    for path in MODEL_PATHS:
        if os.path.exists(path):
            os.remove(path)
    yield
    for path in MODEL_PATHS:
        if os.path.exists(path):
            os.remove(path)


def run_datagen(client, body=None):
    return client.post("/datagen", json=body or {})


def get_anomalies(client):
    r = client.get("/anomalies")
    assert r.status_code == 200
    return r.json()


def test_clean_history_has_structure_but_few_findings(client):
    run_datagen(client, {"scale": "small", "seed": 7})
    data = get_anomalies(client)

    assert data["summary"]["checked"] == 20  # the history trips
    assert data["summary"]["delay_check"] == "statistics"  # no model trained
    assert data["summary"]["flagged"] < 5  # healthy world: almost nothing
    for a in data["anomalies"]:
        assert a["kind"] in ("delay_outlier", "impossible_speed",
                             "contradictory_record")
        assert a["severity"] in ("high", "medium")
        assert a["detail"]


def test_empty_database_scans_to_zero(client):
    data = get_anomalies(client)
    assert data["summary"]["checked"] == 0
    assert data["anomalies"] == []


def test_impossible_trip_is_flagged_high(client):
    run_datagen(client, {"scale": "small", "seed": 7})
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        row = conn.execute(
            "SELECT id FROM shipments WHERE status = 'completed' "
            "AND delay_minutes IS NOT NULL ORDER BY id LIMIT 1"
        ).fetchone()
        conn.execute(
            "UPDATE shipments SET created_at = delivered_at - interval "
            "'5 minutes' WHERE id = %s", (row[0],))

    data = get_anomalies(client)
    hit = next(a for a in data["anomalies"]
               if a["shipment_id"] == row[0]
               and a["kind"] == "impossible_speed")
    assert hit["severity"] == "high"
    assert hit["detected_by"] == "rule"
    assert hit["actual_minutes"] <= 5 + 10


def test_contradictory_record_is_flagged(client):
    run_datagen(client, {"scale": "small", "seed": 7})
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        row = conn.execute(
            "SELECT id FROM shipments WHERE status = 'completed' "
            "ORDER BY id LIMIT 1"
        ).fetchone()
        conn.execute(
            "UPDATE shipments SET created_at = delivered_at + interval "
            "'2 hours' WHERE id = %s", (row[0],))

    data = get_anomalies(client)
    kinds = {a["kind"] for a in data["anomalies"]
             if a["shipment_id"] == row[0]}
    assert "contradictory_record" in kinds
    assert any(a["severity"] == "high" for a in data["anomalies"]
               if a["shipment_id"] == row[0])


def test_huge_delay_is_caught_by_the_model(client):
    run_datagen(client, {"scale": "medium", "seed": 4})
    assert client.post("/model/train", json={}).status_code == 200
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        row = conn.execute(
            "SELECT id FROM shipments WHERE status = 'completed' "
            "AND delay_minutes IS NOT NULL ORDER BY id LIMIT 1"
        ).fetchone()
        conn.execute(
            "UPDATE shipments SET delay_minutes = delay_minutes + 1500 "
            "WHERE id = %s", (row[0],))

    data = get_anomalies(client)
    assert data["summary"]["delay_check"] == "model"
    hit = next(a for a in data["anomalies"]
               if a["shipment_id"] == row[0]
               and a["kind"] == "delay_outlier")
    assert hit["severity"] == "high"
    assert hit["detected_by"] == "model"
    assert hit["actual_minutes"] - hit["expected_minutes"] >= 1500 - 60


def test_scan_is_deterministic(client):
    run_datagen(client, {"scale": "small", "seed": 7})
    first = get_anomalies(client)
    second = get_anomalies(client)
    assert first["anomalies"] == second["anomalies"]


def test_scan_is_audited_and_chain_verifies(client):
    run_datagen(client, {"scale": "small", "seed": 7})
    get_anomalies(client)
    rows = [x for x in client.get("/audit?limit=20").json()
            if x["action"] == "anomaly.scan"]
    assert len(rows) == 1
    assert rows[0]["actor_type"] == "system"
    assert rows[0]["details"]["checked"] == 20
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        assert verify_chain(conn)["ok"] is True


def test_scan_module_pure_function_shape():
    from datetime import datetime, timezone
    from delay_model import travel_minutes
    base = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    features = {"distance_km": 100.0, "max_risk": 0, "avg_risk": 0.0,
                "is_hazardous": 0, "weight_kg": 1000, "legs": 2}
    samples = [{
        "id": 1, "created_at": base,
        "delivered_at": base + timedelta(minutes=10),  # needs 150 min
        "delay_minutes": 30,
    }]
    findings = scan(samples, {1: features}, predict_fn=lambda f: 20)
    assert [f["kind"] for f in findings] == ["impossible_speed"]
    assert travel_minutes(features) == 150
