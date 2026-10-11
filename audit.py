import hashlib
import json
from datetime import datetime, timezone

from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb
from security import current_actor

GENESIS_HASH = "0" * 64  # "previous hash" for the very first row


def compute_hash(prev_hash, created_at, actor_type, actor_id, action,
                 entity_type, entity_id, outcome, details):
    payload = json.dumps(
        {
            "created_at": created_at.astimezone(timezone.utc).isoformat(),
            "actor_type": actor_type, "actor_id": actor_id,
            "action": action, "entity_type": entity_type,
            "entity_id": entity_id, "outcome": outcome, "details": details,
        },
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


def append_audit(conn, actor_type, action, outcome, actor_id=None,
                 entity_type=None, entity_id=None, details=None):
    details = details or {}
    # the authenticated username fills in actor_id automatically for any
    # endpoint that does not name its actor explicitly
    actor_id = actor_id if actor_id is not None else current_actor.get()
    # Turn dates etc. into plain JSON so the hash matches what the DB stores.
    details = json.loads(json.dumps(details, default=str))
    conn.execute("LOCK TABLE audit_log IN EXCLUSIVE MODE")
    last = conn.cursor(row_factory=tuple_row).execute(
        "SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prev_hash = last[0] if last else GENESIS_HASH
    created_at = datetime.now(timezone.utc)
    new_hash = compute_hash(prev_hash, created_at, actor_type, actor_id,
                            action, entity_type, entity_id, outcome, details)
    conn.execute(
        "INSERT INTO audit_log (created_at, actor_type, actor_id, action, "
        "entity_type, entity_id, outcome, details, prev_hash, hash) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (created_at, actor_type, actor_id, action, entity_type, entity_id,
         outcome, Jsonb(details), prev_hash, new_hash),
    )
    return new_hash


def verify_chain(conn):
    rows = conn.cursor(row_factory=tuple_row).execute(
        "SELECT id, created_at, actor_type, actor_id, action, entity_type, "
        "entity_id, outcome, details, prev_hash, hash "
        "FROM audit_log ORDER BY id"
    ).fetchall()
    expected_prev = GENESIS_HASH
    for r in rows:
        if r[9] != expected_prev:
            return {"ok": False, "broken_at_id": r[0], "reason": "prev_hash mismatch"}
        recomputed = compute_hash(r[9], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8])
        if recomputed != r[10]:
            return {"ok": False, "broken_at_id": r[0], "reason": "hash mismatch"}
        expected_prev = r[10]
    return {"ok": True, "checked": len(rows)}