from dataclasses import asdict
from datetime import date
from audit import append_audit, verify_chain
import os

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from pydantic import BaseModel, Field
from guardrails import evaluate, is_allowed
from routing import shortest_route
from typing import Literal

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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

@app.get("/audit")
def list_audit(limit: int = 50):
    limit = max(1, min(limit, 500))  # keep it between 1 and 500 rows
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, created_at, actor_type, actor_id, action, entity_type, "
            "entity_id, outcome, details, hash FROM audit_log "
            "ORDER BY id DESC LIMIT %s",
            (limit,),
        ).fetchall()
    return rows


class RoadEdgeIn(BaseModel):
    from_location_id: int
    to_location_id: int
    distance_km: float = Field(gt=0)
    status: Literal["open", "closed"] = "open"
    risk_score: int = Field(default=0, ge=0, le=100)


@app.get("/road-edges")
def list_road_edges():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, from_location_id, to_location_id, distance_km, status, "
            "risk_score FROM road_edges ORDER BY id"
        ).fetchall()
    return rows


@app.post("/road-edges")
def create_road_edge(edge: RoadEdgeIn):
    if edge.from_location_id == edge.to_location_id:
        raise HTTPException(
            status_code=400, detail="A road must connect two different locations"
        )
    a, b = sorted((edge.from_location_id, edge.to_location_id))
    try:
        with get_connection() as conn:
            row = conn.execute(
                "INSERT INTO road_edges "
                "(from_location_id, to_location_id, distance_km, status, risk_score) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (a, b, edge.distance_km, edge.status, edge.risk_score),
            ).fetchone()
            append_audit(
                conn, "dispatcher", "road_edge.create", "allowed",
                entity_type="road_edge", entity_id=str(row["id"]),
                details={
                    "from": a, "to": b, "distance_km": edge.distance_km,
                    "status": edge.status, "risk_score": edge.risk_score,
                },
            )
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(status_code=400, detail="A location does not exist")
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409, detail="A road between these locations already exists"
        )
    return {
        "id": row["id"], "from_location_id": a, "to_location_id": b,
        "distance_km": edge.distance_km, "status": edge.status,
        "risk_score": edge.risk_score,
    }


@app.get("/route")
def get_route(from_id: int, to_id: int):
    with get_connection() as conn:
        edges = conn.execute(
            "SELECT from_location_id, to_location_id, distance_km, status, "
            "risk_score FROM road_edges"
        ).fetchall()
    result = shortest_route(edges, from_id, to_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail="No open route between these locations"
        )
    return result
