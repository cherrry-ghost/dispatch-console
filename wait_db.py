"""Block until the database accepts connections (used by docker-compose)."""
import os
import time

import psycopg

for attempt in range(60):
    try:
        psycopg.connect(os.environ["DATABASE_URL"]).close()
        print("database is ready")
        break
    except Exception:
        print(f"waiting for database ({attempt + 1})...", flush=True)
        time.sleep(1)
else:
    raise SystemExit("database never became ready")
