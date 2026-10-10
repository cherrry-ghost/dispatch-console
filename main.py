from dataclasses import asdict
from datetime import date
from uuid import uuid4
from audit import append_audit, verify_chain
import os

import psycopg
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from pydantic import BaseModel, Field
from guardrails import evaluate, is_allowed
from routing import shortest_route
from planner import plan_shipments
from typing import Literal
from psycopg.types.json import Jsonb

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

def apply_assignment(conn, req):
    """Close the shipment's old assignment, create the new one, mark shipment assigned."""
    conn.execute(
        "UPDATE assignments SET ended_at = now() "
        "WHERE shipment_id = %s AND ended_at IS NULL",
        (req["shipment_id"],),
    )
    row = conn.execute(
        "INSERT INTO assignments (shipment_id, vehicle_id, driver_id, reason) "
        "VALUES (%s, %s, %s, %s) RETURNING id, started_at",
        (req["shipment_id"], req["vehicle_id"], req["driver_id"], req["reason"]),
    ).fetchone()
    conn.execute(
        "UPDATE shipments SET status = 'assigned' WHERE id = %s",
        (req["shipment_id"],),
    )
    return row


def load_entities(conn, req):
    vehicle = conn.execute(
        "SELECT * FROM vehicles WHERE id = %s", (req["vehicle_id"],)
    ).fetchone()
    driver = conn.execute(
        "SELECT * FROM drivers WHERE id = %s", (req["driver_id"],)
    ).fetchone()
    shipment = conn.execute(
        "SELECT * FROM shipments WHERE id = %s", (req["shipment_id"],)
    ).fetchone()
    return vehicle, driver, shipment


@app.post("/assignments")
def create_assignment(assignment: AssignmentIn, response: Response):
    req = assignment.model_dump()
    with get_connection() as conn:
        vehicle, driver, shipment = load_entities(conn, req)
    if vehicle is None or driver is None or shipment is None:
        raise HTTPException(
            status_code=400, detail="Shipment, vehicle or driver does not exist"
        )
    results = evaluate(vehicle, driver, shipment, date.today())
    entity_id = str(assignment.shipment_id)
    warnings = [asdict(r) for r in results]
    audit_details = {"request": req, "guardrail_results": warnings}
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
                "violations": warnings,
            },
        )
    try:
        with get_connection() as conn:
            if warnings:
                # Risky but not forbidden: park it until a manager decides.
                approval = conn.execute(
                    "INSERT INTO approvals (action, entity_type, entity_id, "
                    "request, warnings, requested_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    ("assignment.create", "shipment", entity_id,
                     Jsonb(req), Jsonb(warnings), "dispatcher"),
                ).fetchone()
                append_audit(
                    conn, "dispatcher", "assignment.request_approval", "pending",
                    entity_type="shipment", entity_id=entity_id,
                    details={**audit_details, "approval_id": approval["id"]},
                )
                response.status_code = 202
                return {
                    "status": "pending_approval",
                    "approval_id": approval["id"],
                    "warnings": warnings,
                }
            row = apply_assignment(conn, req)
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
    return {**row, **req, "warnings": []}


@app.get("/approvals")
def list_approvals(status: str | None = None):
    query = (
        "SELECT id, created_at, action, entity_type, entity_id, request, "
        "warnings, status, requested_by, decided_by, decided_at, decision_note "
        "FROM approvals"
    )
    params = ()
    if status:
        query += " WHERE status = %s"
        params = (status,)
    query += " ORDER BY id DESC"
    with get_connection() as conn:
        return conn.execute(query, params).fetchall()


class DecisionIn(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str = ""


@app.post("/approvals/{approval_id}/decision")
def decide_approval(approval_id: int, body: DecisionIn):
    blocked = None
    with get_connection() as conn:
        ap = conn.execute(
            "SELECT * FROM approvals WHERE id = %s FOR UPDATE", (approval_id,)
        ).fetchone()
        if ap is None:
            raise HTTPException(status_code=404, detail="Approval not found")
        if ap["status"] != "pending":
            raise HTTPException(status_code=409, detail="Already decided")
        req = ap["request"]
        details = {"approval_id": approval_id, "note": body.note, "request": req}
        if body.decision == "approved":
            # Data may have changed since the request (e.g. a licence expired).
            vehicle, driver, shipment = load_entities(conn, req)
            if vehicle is None or driver is None or shipment is None:
                raise HTTPException(status_code=400, detail="Entity no longer exists")
            results = evaluate(vehicle, driver, shipment, date.today())
            if not is_allowed(results):
                blocked = [asdict(r) for r in results]
                append_audit(
                    conn, "manager", "approval.decide", "blocked",
                    entity_type="shipment", entity_id=ap["entity_id"],
                    details={**details, "guardrail_results": blocked},
                )
            else:
                apply_assignment(conn, req)
        if blocked is None:
            conn.execute(
                "UPDATE approvals SET status = %s, decided_by = 'manager', "
                "decided_at = now(), decision_note = %s WHERE id = %s",
                (body.decision, body.note, approval_id),
            )
            append_audit(
                conn, "manager", f"approval.{body.decision}", body.decision,
                entity_type="shipment", entity_id=ap["entity_id"],
                details=details,
            )
    if blocked:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Cannot approve: guardrails now block this assignment",
                "violations": blocked,
            },
        )
    return {"approval_id": approval_id, "status": body.decision}


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


class PlannerRequest(BaseModel):
    shipment_ids: list[int] | None = None  # None = all pending shipments


@app.post("/planner/propose")
def propose_plan(body: PlannerRequest):
    with get_connection() as conn:
        vehicles = conn.execute(
            "SELECT * FROM vehicles ORDER BY id"
        ).fetchall()
        if body.shipment_ids is None:
            shipments = conn.execute(
                "SELECT * FROM shipments WHERE status = 'pending' ORDER BY id"
            ).fetchall()
        else:
            shipments = conn.execute(
                "SELECT * FROM shipments WHERE status = 'pending' "
                "AND id = ANY(%s) ORDER BY id",
                (body.shipment_ids,),
            ).fetchall()
        plan = plan_shipments(shipments, vehicles, date.today())
        run_id = uuid4().hex[:12]
        # The planner is an AI actor: the input snapshot, the recommendation
        # and the skips are recorded before a human decides anything.
        append_audit(
            conn, "ai_planner", "plan.propose", "allowed",
            entity_type="plan", entity_id=run_id,
            details={
                "model": "ortools-cp-sat", "model_version": 1,
                "input": {
                    "shipment_ids": [s["id"] for s in shipments],
                    "vehicle_ids": [v["id"] for v in vehicles],
                },
                "proposal": plan["proposals"],
                "vehicles": plan["vehicles"],
                "skipped": plan["skipped"],
                "summary": plan["summary"],
            },
        )
    return {"plan_id": run_id, **plan}
