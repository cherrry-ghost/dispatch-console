import hashlib
import os

import psycopg

from audit import verify_chain
from main import EVIDENCE_DIR, MAX_EVIDENCE_BYTES
from test_road_edges import make_locations, road


def make_incident(client, road_edge_id=None, description="Protest blocking road"):
    body = {"description": description, "incident_type": "protest"}
    if road_edge_id is not None:
        body["road_edge_id"] = road_edge_id
    return client.post("/incidents", json=body)


def make_road(client):
    a, b = make_locations(client, 2)
    road(client, a, b, 10)
    return client.get("/road-edges").json()[0]["id"]


def actions(client, prefix):
    return [r for r in client.get("/audit?limit=50").json()
            if r["action"].startswith(prefix)]


def test_report_incident_is_audited(client):
    edge = make_road(client)
    r = make_incident(client, road_edge_id=edge)

    assert r.status_code == 200
    assert r.json()["status"] == "reported"
    logged = actions(client, "incident.reported")
    assert len(logged) == 1
    assert logged[0]["details"]["road_edge_id"] == edge


def test_report_unknown_road_is_400(client):
    assert make_incident(client, road_edge_id=999).status_code == 400


def test_verify_closes_the_road_and_is_audited(client):
    edge = make_road(client)
    incident_id = make_incident(client, road_edge_id=edge).json()["id"]

    r = client.post(f"/incidents/{incident_id}/verify")

    assert r.status_code == 200
    assert r.json()["road_closed"] is True
    assert client.get("/road-edges").json()[0]["status"] == "closed"
    logged = actions(client, "incident.verified")
    assert logged[0]["actor_type"] == "manager"
    assert logged[0]["details"]["road_closed"] is True


def test_resolve_reopens_the_road(client):
    edge = make_road(client)
    incident_id = make_incident(client, road_edge_id=edge).json()["id"]
    client.post(f"/incidents/{incident_id}/verify")

    r = client.post(f"/incidents/{incident_id}/resolve")

    assert r.status_code == 200
    assert r.json()["road_reopened"] is True
    assert client.get("/road-edges").json()[0]["status"] == "open"


def test_lifecycle_cannot_skip_states(client):
    edge = make_road(client)
    incident_id = make_incident(client, road_edge_id=edge).json()["id"]

    # resolve straight from reported is illegal
    assert client.post(f"/incidents/{incident_id}/resolve").status_code == 409
    assert client.post(f"/incidents/{incident_id}/verify").status_code == 200
    # verify twice is illegal
    assert client.post(f"/incidents/{incident_id}/verify").status_code == 409


def test_dismiss_keeps_road_open_and_blocks_later_verify(client):
    edge = make_road(client)
    incident_id = make_incident(client, road_edge_id=edge).json()["id"]

    r = client.post(f"/incidents/{incident_id}/dismiss")

    assert r.status_code == 200
    assert client.get("/road-edges").json()[0]["status"] == "open"
    assert client.post(f"/incidents/{incident_id}/verify").status_code == 409


def test_verify_without_road_works(client):
    incident_id = make_incident(client).json()["id"]
    r = client.post(f"/incidents/{incident_id}/verify")
    assert r.status_code == 200
    assert r.json()["road_closed"] is False


def test_evidence_is_optional_and_hash_is_audited(client):
    edge = make_road(client)
    incident_id = make_incident(client, road_edge_id=edge).json()["id"]

    # verify and resolve with NO evidence attached at all
    assert client.post(f"/incidents/{incident_id}/verify").status_code == 200
    assert client.post(f"/incidents/{incident_id}/resolve").status_code == 200
    assert client.get(f"/incidents/{incident_id}").json()["evidence"] == []

    # now a full run with a photo: the hash must land in the audit trail
    incident_id2 = make_incident(client, road_edge_id=edge,
                                 description="Flooded underpass").json()["id"]
    data = b"pretend-jpeg-bytes"
    r = client.post(
        f"/incidents/{incident_id2}/evidence",
        files={"file": ("road_photo.jpg", data, "image/jpeg")},
    )
    assert r.status_code == 200
    assert r.json()["sha256"] == hashlib.sha256(data).hexdigest()

    stored = client.get(f"/incidents/{incident_id2}").json()["evidence"]
    assert len(stored) == 1
    assert stored[0]["size_bytes"] == len(data)

    logged = actions(client, "evidence.attached")
    assert logged[0]["details"]["sha256"] == hashlib.sha256(data).hexdigest()

    saved = os.path.join(EVIDENCE_DIR,
                         f"{r.json()['sha256']}_road_photo.jpg")
    assert os.path.exists(saved)
    os.remove(saved)  # keep the machine clean after the test

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        assert verify_chain(conn)["ok"] is True


def test_evidence_rejected_after_dismissed(client):
    incident_id = make_incident(client).json()["id"]
    client.post(f"/incidents/{incident_id}/dismiss")
    r = client.post(
        f"/incidents/{incident_id}/evidence",
        files={"file": ("late.jpg", b"bytes", "image/jpeg")},
    )
    assert r.status_code == 409


def test_evidence_too_large_is_rejected(client, monkeypatch):
    incident_id = make_incident(client).json()["id"]
    monkeypatch.setattr("main.MAX_EVIDENCE_BYTES", 10)
    r = client.post(
        f"/incidents/{incident_id}/evidence",
        files={"file": ("big.mp4", b"x" * 11, "video/mp4")},
    )
    assert r.status_code == 413
    assert MAX_EVIDENCE_BYTES >= 20 * 1024 * 1024  # real limit untouched


def test_manual_road_status_endpoint(client):
    edge = make_road(client)
    r = client.post(f"/road-edges/{edge}/status", json={"status": "closed"})
    assert r.status_code == 200
    assert client.get("/road-edges").json()[0]["status"] == "closed"
    logged = actions(client, "road_edge.status_change")
    assert logged[0]["details"]["status"] == "closed"
    assert client.post(f"/road-edges/999/status",
                       json={"status": "open"}).status_code == 404
