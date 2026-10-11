import os

import psycopg
import pytest
from fastapi.testclient import TestClient

# Point the app at the TEST database, never the real one.
base_url = os.environ["DATABASE_URL"]
if not base_url.endswith("/dispatch"):
    raise RuntimeError("DATABASE_URL must end with /dispatch before running tests")
os.environ["DATABASE_URL"] = base_url[: -len("/dispatch")] + "/dispatch_test"

# psycopg itself does not understand the "+psycopg" driver suffix
_TEST_DB_URL = os.environ["DATABASE_URL"].replace("+psycopg", "")

from main import app  # noqa: E402
import security  # noqa: E402


# precomputed once (PBKDF2 at 600k iterations is too slow per-test)
_SEED_USERS = [
    ("admin", security.hash_secret("admin123"), "admin"),
    ("dispatcher", security.hash_secret("dispatch123"), "dispatcher"),
    ("manager", security.hash_secret("manager123"), "manager"),
]


@pytest.fixture(autouse=True)
def clean_database():
    """Empty every table before each test so each one starts from a blank slate."""
    with psycopg.connect(_TEST_DB_URL) as conn:
        conn.execute(
           "TRUNCATE audit_log, approvals, copilot_proposals, media_checks, incident_evidence, incidents, zones, playbooks, notifications, events, vehicle_positions, road_edges, assignments, shipments, drivers, vehicles, locations, users "
            "RESTART IDENTITY"
        )
        # the login fixtures need the seeded users; truncating users keeps
        # tests hermetic, so re-seed the three console accounts here
        for username, pwd_hash, role in _SEED_USERS:
            conn.execute(
                "INSERT INTO users (username, password_hash, role) "
                "VALUES (%s, %s, %s)",
                (username, pwd_hash, role),
            )
    yield


class AuthClient(TestClient):
    """TestClient that sends a stored Bearer token with every request."""

    def __init__(self, *args, token=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.token = token

    def request(self, *args, **kwargs):
        if self.token:
            headers = kwargs.get("headers") or {}
            headers["Authorization"] = f"Bearer {self.token}"
            kwargs["headers"] = headers
        return super().request(*args, **kwargs)


def _login(username: str, password: str) -> str:
    with TestClient(app) as c:
        response = c.post("/login",
                          json={"username": username, "password": password})
        assert response.status_code == 200, response.text
        return response.json()["token"]


@pytest.fixture
def client():
    """Dispatcher-authenticated client (default for existing tests)."""
    return AuthClient(app, token=_login("dispatcher", "dispatch123"))


@pytest.fixture
def anon_client():
    """No token - for testing that protected endpoints reject it."""
    return AuthClient(app)


@pytest.fixture
def manager_client():
    return AuthClient(app, token=_login("manager", "manager123"))


@pytest.fixture
def admin_client():
    return AuthClient(app, token=_login("admin", "admin123"))


@pytest.fixture
def driver_login():
    """Helper: driver_login(driver_id) -> AuthClient with a driver token."""
    def _make(driver_id: int) -> AuthClient:
        with TestClient(app) as c:
            response = c.post("/driver/login",
                              json={"driver_id": driver_id, "pin": "1234"})
            assert response.status_code == 200, response.text
        return AuthClient(app, token=response.json()["token"])
    return _make
