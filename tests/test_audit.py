import os

import psycopg
import pytest
from psycopg.rows import dict_row

from audit import append_audit, verify_chain


def make_rows(conn, n=3):
    for i in range(n):
        append_audit(conn, "dispatcher", "test.action", "allowed",
                     actor_id=f"d{i}", details={"n": i})


def test_chain_links_and_verifies():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        make_rows(conn)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        assert verify_chain(conn) == {"ok": True, "checked": 3}
        rows = conn.execute("SELECT prev_hash, hash FROM audit_log ORDER BY id").fetchall()
        assert rows[1][0] == rows[0][1]  # row 2 points at row 1


def test_trigger_blocks_update():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        make_rows(conn, 1)
    with pytest.raises(psycopg.errors.RaiseException):
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            conn.execute("UPDATE audit_log SET action = 'hacked'")


def test_tampering_is_detected():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        make_rows(conn)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        # Simulate an attacker with admin rights who switches the trigger off.
        conn.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only")
        conn.execute("UPDATE audit_log SET outcome = 'allowed-edited' WHERE id = 2")
        conn.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        result = verify_chain(conn)
        assert result["ok"] is False
        assert result["broken_at_id"] == 2

def test_deleted_row_is_detected():
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        make_rows(conn)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only")
        conn.execute("DELETE FROM audit_log WHERE id = 2")
        conn.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        result = verify_chain(conn)
        assert result == {"ok": False, "broken_at_id": 3, "reason": "prev_hash mismatch"}


def test_works_with_dict_row_connections():
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        make_rows(conn, 2)
        assert verify_chain(conn) == {"ok": True, "checked": 2}