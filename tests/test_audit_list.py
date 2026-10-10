import os

import psycopg

from audit import append_audit


def add_entries(count):
    """Write `count` audit entries straight into the test database."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        for i in range(count):
            append_audit(
                conn, "dispatcher", f"test.action{i}", "allowed",
                entity_type="thing", entity_id=str(i),
            )


def test_audit_list_empty(client):
    response = client.get("/audit")
    assert response.status_code == 200
    assert response.json() == []


def test_audit_list_newest_first(client):
    add_entries(3)
    rows = client.get("/audit").json()
    assert [r["id"] for r in rows] == [3, 2, 1]
    assert rows[0]["action"] == "test.action2"


def test_audit_list_respects_limit(client):
    add_entries(5)
    assert len(client.get("/audit?limit=2").json()) == 2
    # limit=0 is clamped up to 1, so you never get an empty page by mistake
    assert len(client.get("/audit?limit=0").json()) == 1