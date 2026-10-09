from dataclasses import asdict
from datetime import date
from audit import append_audit, verify_chain
import os

import psycopg
from fastapi import FastAPI, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field
from guardrails import evaluate, is_allowed

app = FastAPI()


class VehicleIn(BaseModel):
    plate: str
    vehicle_type: str
    capacity_kg: int
    insurance_expiry: date | None = None
    fitness_expiry: date | None = None


def get_connection():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


@app.get("/")
def home():
    return {"message": "Dispatch console is running"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/vehicles")
def list_vehicles():
    with get_connection() as conn:
        rows = conn.execute(
                        "SELECT id, plate, vehicle_type, capacity_kg, insurance_expiry, "
            "fitness_expiry FROM vehicles ORDER BY id"
        ).fetchall()
    return rows


@app.post("/vehicles")
def create_vehicle(vehicle: VehicleIn):
    try:
        with get_connection() as conn:
            row = conn.execute(
                "INSERT INTO vehicles "
                "(plate, vehicle_type, capacity_kg, insurance_expiry, fitness_expiry) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (
                    vehicle.plate,
                    vehicle.vehicle_type,
                    vehicle.capacity_kg,
                    vehicle.insurance_expiry,
                    vehicle.fitness_expiry,
                ),
            ).fetchone()
            append_audit(
                conn, "dispatcher", "vehicle.create", "allowed",
                entity_type="vehicle", entity_id=str(row["id"]),
                details={"plate": vehicle.plate},
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409, detail="A vehicle with this plate already exists"
        )
    return {"id": row["id"], **vehicle.model_dump()}

class DriverIn(BaseModel):
    full_name: str
    licence_number: str
    licence_expiry: date
    hazmat_certified: bool = False


@app.get("/drivers")
def list_drivers():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, full_name, licence_number, licence_expiry, hazmat_certified "
            "FROM drivers ORDER BY id"
        ).fetchall()
    return rows


@app.post("/drivers")
def create_driver(driver: DriverIn):
    try:
        with get_connection() as conn:
            row = conn.execute(
                "INSERT INTO drivers "
                "(full_name, licence_number, licence_expiry, hazmat_certified) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (
                    driver.full_name,
                    driver.licence_number,
                    driver.licence_expiry,
                    driver.hazmat_certified,
                ),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409, detail="A driver with this licence number already exists"
        )
    return {"id": row["id"], **driver.model_dump()}

class LocationIn(BaseModel):
    name: str
    location_type: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


@app.get("/locations")
def list_locations():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, location_type, latitude, longitude "
            "FROM locations ORDER BY id"
        ).fetchall()
    return rows


@app.post("/locations")
def create_location(location: LocationIn):
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO locations (name, location_type, latitude, longitude) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (
                location.name,
                location.location_type,
                location.latitude,
                location.longitude,
            ),
        ).fetchone()
    return {"id": row["id"], **location.model_dump()}

class ShipmentIn(BaseModel):
    description: str
    weight_kg: int = Field(gt=0)
    is_hazardous: bool = False
    origin_id: int
    destination_id: int
    required_vehicle_type: str | None = None


@app.get("/shipments")
def list_shipments():
    with get_connection() as conn:
        rows = conn.execute(
                        "SELECT id, description, weight_kg, is_hazardous, origin_id, "
            "destination_id, required_vehicle_type, status, created_at "
            "FROM shipments ORDER BY id"
        ).fetchall()
    return rows


@app.post("/shipments")
def create_shipment(shipment: ShipmentIn):
    if shipment.origin_id == shipment.destination_id:
        raise HTTPException(
            status_code=400, detail="Origin and destination must be different"
        )
    try:
        with get_connection() as conn:
            row = conn.execute(
                                "INSERT INTO shipments "
                "(description, weight_kg, is_hazardous, origin_id, destination_id, "
                "required_vehicle_type) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, status, created_at",
                (
                    shipment.description,
                    shipment.weight_kg,
                    shipment.is_hazardous,
                    shipment.origin_id,
                    shipment.destination_id,
                    shipment.required_vehicle_type,
                ),
            ).fetchone()
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(
            status_code=400, detail="Origin or destination location does not exist"
        )
    return {**row, **shipment.model_dump()}

class AssignmentIn(BaseModel):
    shipment_id: int
    vehicle_id: int
    driver_id: int
    reason: str = Field(min_length=1)


@app.get("/assignments")
def list_assignments():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, shipment_id, vehicle_id, driver_id, started_at, "
            "ended_at, reason FROM assignments ORDER BY id"
        ).fetchall()
    return rows

@app.post("/assignments")
def create_assignment(assignment: AssignmentIn):
    with get_connection() as conn:
        vehicle = conn.execute(
            "SELECT * FROM vehicles WHERE id = %s", (assignment.vehicle_id,)
        ).fetchone()
        driver = conn.execute(
            "SELECT * FROM drivers WHERE id = %s", (assignment.driver_id,)
        ).fetchone()
        shipment = conn.execute(
            "SELECT * FROM shipments WHERE id = %s", (assignment.shipment_id,)
        ).fetchone()
    if vehicle is None or driver is None or shipment is None:
        raise HTTPException(
            status_code=400, detail="Shipment, vehicle or driver does not exist"
        )
    results = evaluate(vehicle, driver, shipment, date.today())
    entity_id = str(assignment.shipment_id)
    audit_details = {
        "request": assignment.model_dump(),
        "guardrail_results": [asdict(r) for r in results],
    }
    if not is_allowed(results):
        with get_connection() as conn:  # commits BEFORE the raise below
            append_audit(
                conn, "dispatcher", "assignment.create", "blocked",
                entity_type="shipment", entity_id=entity_id,
                details=audit_details,
            )
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Assignment blocked by guardrails",
                "violations": [asdict(r) for r in results],
            },
        )
    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE assignments SET ended_at = now() "
                "WHERE shipment_id = %s AND ended_at IS NULL",
                (assignment.shipment_id,),
            )
            row = conn.execute(
                "INSERT INTO assignments (shipment_id, vehicle_id, driver_id, reason) "
                "VALUES (%s, %s, %s, %s) RETURNING id, started_at",
                (
                    assignment.shipment_id,
                    assignment.vehicle_id,
                    assignment.driver_id,
                    assignment.reason,
                ),
            ).fetchone()
            conn.execute(
                "UPDATE shipments SET status = 'assigned' WHERE id = %s",
                (assignment.shipment_id,),
            )
            append_audit(  # same transaction as the assignment
                conn, "dispatcher", "assignment.create", "allowed",
                entity_type="shipment", entity_id=entity_id,
                details=audit_details,
            )
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(
            status_code=400,
            detail="Shipment, vehicle or driver does not exist",
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail="This shipment already has an active assignment",
        )
    return {**row, **assignment.model_dump(), "warnings": [asdict(r) for r in results]}


@app.get("/audit/verify")
def verify_audit():
    with get_connection() as conn:
        return verify_chain(conn)