import os

import psycopg

from audit import verify_chain


def make_vehicle(client):
    response = client.post(
        "/vehicles",
        json={
            "plate": "TN01AB1234",
            "vehicle_type": "truck",
            "capacity_kg": 5000,
            "insurance_expiry": "2035-12-31",
            "fitness_expiry": "2035-12-31",
        },
    )
    return response.json()["id"]


def make_driver(client, licence):
    response = client.post(
        "/drivers",
        json={
            "full_name": "Test Driver",
            "licence_number": licence,
            "licence_expiry": "2035-06-30",
        },
    )
    return response.json()["id"]


def make_shipment(client, weight=2000):
    origin = client.post(
        "/locations",
        json={
            "name": "Depot",
            "location_type": "depot",
            "latitude": 13.0827,
            "longitude": 80.2707,
        },
    ).json()["id"]
    destination = client.post(
        "/locations",
        json={
            "name": "Warehouse",
            "location_type": "warehouse",
            "latitude": 11.0168,
            "longitude": 76.9558,
        },
    ).json()["id"]
    response = client.post(
        "/shipments",
        json={
            "description": "Rice bags",
            "weight_kg": weight,
            "origin_id": origin,
            "destination_id": destination,
        },
    )
    return response.json()["id"]


def assign(client, shipment, vehicle, driver, reason):
    return client.post(
        "/assignments",
        json={
            "shipment_id": shipment,
            "vehicle_id": vehicle,
            "driver_id": driver,
            "reason": reason,
        },
    )


def audit_outcomes(action=None):
    """Return (outcomes in order, verify_chain result). Optionally filter by action."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        if action:
            rows = conn.execute(
                "SELECT outcome FROM audit_log WHERE action = %s ORDER BY id",
                (action,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT outcome FROM audit_log ORDER BY id").fetchall()
        return [r[0] for r in rows], verify_chain(conn)

def test_reassignment_keeps_history(client):
    vehicle = make_vehicle(client)
    ravi = make_driver(client, "TN0120250001")
    suresh = make_driver(client, "TN0120250002")
    shipment = make_shipment(client)

    assign(client, shipment, vehicle, ravi, "first assignment")
    assign(client, shipment, vehicle, suresh, "Ravi fell sick")

    rows = client.get("/assignments").json()
    assert len(rows) == 2
    assert rows[0]["ended_at"] is not None
    assert rows[1]["ended_at"] is None
    assert rows[1]["driver_id"] == suresh
    assert client.get("/shipments").json()[0]["status"] == "assigned"

    outcomes, result = audit_outcomes("assignment.create")
    assert outcomes == ["allowed", "allowed"]
    assert result["ok"] is True


def test_failed_assignment_keeps_current_one(client):
    vehicle = make_vehicle(client)
    ravi = make_driver(client, "TN0120250001")
    shipment = make_shipment(client)

    assign(client, shipment, vehicle, ravi, "first assignment")
    bad = assign(client, shipment, 99, ravi, "bad vehicle")

    assert bad.status_code == 400
    rows = client.get("/assignments").json()
    assert len(rows) == 1
    assert rows[0]["ended_at"] is None


def test_overweight_assignment_is_blocked(client):
    vehicle = make_vehicle(client)
    ravi = make_driver(client, "TN0120250001")
    shipment = make_shipment(client, weight=9000)

    response = assign(client, shipment, vehicle, ravi, "too heavy")

    assert response.status_code == 422
    assert response.json()["detail"]["violations"][0]["rule"] == "capacity"
    assert client.get("/assignments").json() == []
    assert client.get("/shipments").json()[0]["status"] == "pending"

    outcomes, result = audit_outcomes("assignment.create")
    assert outcomes == ["blocked"]
    assert result["ok"] is True


def test_vehicle_without_dates_is_blocked(client):
    vehicle = client.post(
        "/vehicles",
        json={"plate": "TN02CD5678", "vehicle_type": "truck", "capacity_kg": 5000},
    ).json()["id"]
    ravi = make_driver(client, "TN0120250001")
    shipment = make_shipment(client)

    response = assign(client, shipment, vehicle, ravi, "no paperwork")