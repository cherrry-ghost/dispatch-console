"""Apply every migrations/*.sql file once, in order, to DATABASE_URL.

Idempotent enough for the demo: each migration file is only applied if its
number has not been recorded yet (applied_migrations table). Used by the
docker-compose setup and handy locally too:  python migrate.py
"""
import glob
import os
import re

import psycopg

conn = psycopg.connect(os.environ["DATABASE_URL"])
conn.execute("""CREATE TABLE IF NOT EXISTS applied_migrations (
                    filename TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
done = {r[0] for r in conn.execute("SELECT filename FROM applied_migrations")
        .fetchall()}
files = sorted(glob.glob(os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "migrations", "*.sql")))

# First run against a database that was migrated by hand (before this
# tracking table existed): the schema is already there, so just record it.
if not done and conn.execute(
        "SELECT to_regclass('public.users')").fetchone()[0] is not None:
    for path in files:
        conn.execute("INSERT INTO applied_migrations (filename) VALUES (%s)",
                     (os.path.basename(path),))
    conn.commit()
    print("existing schema detected - marked all migrations as applied")
    conn.close()
    raise SystemExit(0)

for path in files:
    name = os.path.basename(path)
    if name in done:
        continue
    print(f"applying {name} ...", flush=True)
    with open(path) as f:
        conn.execute(f.read())
    conn.execute("INSERT INTO applied_migrations (filename) VALUES (%s)",
                 (name,))
    conn.commit()
    print(f"applied {name}", flush=True)
conn.close()
print("migrations up to date")
