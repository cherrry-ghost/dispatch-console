"""Close leftover demo jobs: end driver 1's never-delivered assignments.

Every practice run of demo.py assigns a shipment to the same clean
vehicle + driver combo and never delivers it, so the driver app list
grows. This tool closes those leftovers (and completes their shipments)
and records one audit entry, keeping the hash chain intact.

Usage:  python tools/close_demo_jobs.py [driver_id]
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from audit import append_audit  # noqa: E402


def get_connection():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def main(driver_id: int = 1) -> None:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT a.id, a.shipment_id FROM assignments a "
            "WHERE a.driver_id = %s AND a.ended_at IS NULL ORDER BY a.id",
            (driver_id,),
        ).fetchall()
        for row in rows:
            assignment_id, shipment_id = row["id"], row["shipment_id"]
            conn.execute(
                "UPDATE assignments SET ended_at = now(), "
                "delivered_at = COALESCE(delivered_at, now()) "
                "WHERE id = %s", (assignment_id,))
            open_left = conn.execute(
                "SELECT count(*) AS n FROM assignments "
                "WHERE shipment_id = %s AND ended_at IS NULL",
                (shipment_id,)).fetchone()["n"]
            if open_left == 0:
                conn.execute(
                    "UPDATE shipments SET status = 'completed', "
                    "delivered_at = COALESCE(delivered_at, now()) "
                    "WHERE id = %s AND status IN ('assigned', 'in_transit')",
                    (shipment_id,))
        append_audit(conn, "system", "demo.cleanup", "allowed",
                     actor_id="system", entity_type="driver",
                     entity_id=str(driver_id),
                     details={"closed_assignments": len(rows),
                              "reason": "leftover demo jobs closed at "
                                        "owner request"})
        conn.commit()
    print(f"Closed {len(rows)} open job(s) for driver {driver_id}; "
          f"audit entry appended.")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
