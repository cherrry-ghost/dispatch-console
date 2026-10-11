import os

import psycopg
import pytest
from fastapi.testclient import TestClient

# Point the app at the TEST database, never the real one.
base_url = os.environ["DATABASE_URL"]
if not base_url.endswith("/dispatch"):
    raise RuntimeError("DATABASE_URL must end with /dispatch before running tests")
os.environ["DATABASE_URL"] = base_url[: -len("/dispatch")] + "/dispatch_test"

from main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    """Empty every table before each test so each one starts from a blank slate."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(
           "TRUNCATE audit_log, approvals, incident_evidence, incidents, zones, playbooks, events, vehicle_positions, road_edges, assignments, shipments, drivers, vehicles, locations "
            "RESTART IDENTITY"
        )
    yield


@pytest.fixture
def client():
    return TestClient(app)
