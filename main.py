from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from audit import append_audit, verify_chain
import hashlib
import os

import psycopg
from fastapi import FastAPI, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from pydantic import BaseModel, Field
from guardrails import evaluate, is_allowed, route_label
from routing import shortest_route, ranked_routes
from planner import plan_shipments
from zones import split_edges_by_zones
from validator import validate_route
from datagen import generate_world
from delay_model import (
    FEATURES, MIN_HISTORY_FOR_TRAINING, MODEL_PATH, MODEL_VERSION,
    load as load_model, mean_absolute_error as delay_model_mae,
    model_exists, predict as predict_delay, route_features,
    save as save_delay_model, train as train_delay_model, travel_minutes,
)
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
            "destination_id, required_vehicle_type, status, created_at, "
            "delivered_at, eta_minutes, delay_minutes "
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
        entity_type = "route" if ap["action"] == "route.use" else "shipment"
        details = {"approval_id": approval_id, "note": body.note, "request": req}
        if body.decision == "approved":
            if ap["action"] == "route.use":
                # Re-check the EXACT route that was requested: a zone,
                # closure or incident may have appeared since the request.
                requested_path = req.get("path") or []
                edges = conn.execute(
                    "SELECT * FROM road_edges").fetchall()
                locations = {
                    r["id"]: r
                    for r in conn.execute(
                        "SELECT id, latitude, longitude FROM locations"
                    ).fetchall()
                }
                zones = conn.execute(
                    "SELECT * FROM zones WHERE active ORDER BY id"
                ).fetchall()
                open_pairs = {
                    (min(e["from_location_id"], e["to_location_id"]),
                     max(e["from_location_id"], e["to_location_id"]))
                    for e in edges if e["status"] == "open"
                }
                legs_open = all(
                    (min(a, b), max(a, b)) in open_pairs
                    for a, b in zip(requested_path, requested_path[1:])
                )
                violations = validate_route(requested_path, zones, locations)
                if legs_open and not violations:
                    details["route"] = {"path": requested_path}
                else:
                    blocked = violations or [{
                        "rule": "route_gone",
                        "message": "A road on the requested route is now "
                                   "closed.",
                    }]
                    append_audit(
                        conn, "manager", "approval.decide", "blocked",
                        entity_type="route", entity_id=ap["entity_id"],
                        details={**details, "guardrail_results": blocked},
                    )
            else:
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
                entity_type=entity_type, entity_id=ap["entity_id"],
                details=details,
            )
    if blocked:
        message = ("Cannot approve: this route is no longer usable"
                   if entity_type == "route"
                   else "Cannot approve: guardrails now block this assignment")
        raise HTTPException(
            status_code=422,
            detail={"message": message, "violations": blocked},
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


class ZoneIn(BaseModel):
    name: str
    center_lat: float = Field(ge=-90, le=90)
    center_lng: float = Field(ge=-180, le=180)
    radius_km: float = Field(gt=0)


@app.post("/zones")
def create_zone(zone: ZoneIn):
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO zones (name, center_lat, center_lng, radius_km) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (zone.name, zone.center_lat, zone.center_lng, zone.radius_km),
        ).fetchone()
        append_audit(
            conn, "dispatcher", "zone.create", "allowed",
            entity_type="zone", entity_id=str(row["id"]),
            details=zone.model_dump(),
        )
    return {"id": row["id"], **zone.model_dump(), "active": True}


@app.get("/zones")
def list_zones():
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, name, center_lat, center_lng, radius_km, active, "
            "created_at FROM zones ORDER BY id"
        ).fetchall()


class ZoneStatusIn(BaseModel):
    active: bool


@app.post("/zones/{zone_id}/status")
def set_zone_status(zone_id: int, body: ZoneStatusIn):
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE zones SET active = %s WHERE id = %s RETURNING id, name",
            (body.active, zone_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Zone not found")
        append_audit(
            conn, "dispatcher", "zone.activate" if body.active
            else "zone.deactivate", "allowed",
            entity_type="zone", entity_id=str(zone_id),
            details={"name": row["name"], "active": body.active},
        )
    return {"id": zone_id, "name": row["name"], "active": body.active}


class RoadStatusIn(BaseModel):
    status: Literal["open", "closed"]


@app.post("/road-edges/{edge_id}/status")
def set_road_status(edge_id: int, body: RoadStatusIn):
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE road_edges SET status = %s WHERE id = %s "
            "RETURNING from_location_id, to_location_id",
            (body.status, edge_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Road not found")
        append_audit(
            conn, "dispatcher", "road_edge.status_change", "allowed",
            entity_type="road_edge", entity_id=str(edge_id),
            details={"road": [row["from_location_id"],
                              row["to_location_id"]],
                     "status": body.status},
        )
    return {"id": edge_id, "status": body.status}


class IncidentIn(BaseModel):
    description: str = Field(min_length=1)
    incident_type: str
    road_edge_id: int | None = None


@app.post("/incidents")
def report_incident(incident: IncidentIn):
    try:
        with get_connection() as conn:
            row = conn.execute(
                "INSERT INTO incidents (description, incident_type, "
                "road_edge_id) VALUES (%s, %s, %s) RETURNING id, status, "
                "reported_at",
                (incident.description, incident.incident_type,
                 incident.road_edge_id),
            ).fetchone()
            append_audit(
                conn, "dispatcher", "incident.reported", "allowed",
                entity_type="incident", entity_id=str(row["id"]),
                details=incident.model_dump(),
            )
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(status_code=400, detail="Road does not exist")
    return {"id": row["id"], "status": row["status"],
            "reported_at": row["reported_at"], **incident.model_dump()}


@app.get("/incidents")
def list_incidents(status: str | None = None):
    query = (
        "SELECT i.id, i.description, i.incident_type, i.road_edge_id, "
        "i.status, i.reported_by, i.reported_at, i.decided_by, i.decided_at, "
        "i.resolved_at, "
        "(SELECT count(*) FROM incident_evidence e "
        " WHERE e.incident_id = i.id) AS evidence_count "
        "FROM incidents i"
    )
    params = ()
    if status:
        query += " WHERE i.status = %s"
        params = (status,)
    query += " ORDER BY i.id DESC"
    with get_connection() as conn:
        return conn.execute(query, params).fetchall()


def _load_incident(conn, incident_id):
    row = conn.execute(
        "SELECT * FROM incidents WHERE id = %s FOR UPDATE", (incident_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return row


def _set_road_for_incident(conn, road_edge_id, closed):
    conn.execute(
        "UPDATE road_edges SET status = %s WHERE id = %s",
        ("closed" if closed else "open", road_edge_id),
    )


@app.post("/incidents/{incident_id}/verify")
def verify_incident(incident_id: int):
    with get_connection() as conn:
        inc = _load_incident(conn, incident_id)
        if inc["status"] != "reported":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot verify an incident that is {inc['status']}",
            )
        conn.execute(
            "UPDATE incidents SET status = 'verified', decided_by = 'manager', "
            "decided_at = now() WHERE id = %s", (incident_id,),
        )
        road_closed = inc["road_edge_id"] is not None
        if road_closed:
            _set_road_for_incident(conn, inc["road_edge_id"], True)
        append_audit(
            conn, "manager", "incident.verified", "allowed",
            entity_type="incident", entity_id=str(incident_id),
            details={"description": inc["description"],
                     "road_edge_id": inc["road_edge_id"],
                     "road_closed": road_closed,
                     "evidence_count": conn.execute(
                         "SELECT count(*) AS n FROM incident_evidence "
                         "WHERE incident_id = %s", (incident_id,),
                     ).fetchone()["n"]},
        )
    return {"id": incident_id, "status": "verified",
            "road_closed": road_closed}


@app.post("/incidents/{incident_id}/dismiss")
def dismiss_incident(incident_id: int):
    with get_connection() as conn:
        inc = _load_incident(conn, incident_id)
        if inc["status"] != "reported":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot dismiss an incident that is {inc['status']}",
            )
        conn.execute(
            "UPDATE incidents SET status = 'dismissed', decided_by = 'manager', "
            "decided_at = now() WHERE id = %s", (incident_id,),
        )
        append_audit(
            conn, "manager", "incident.dismissed", "allowed",
            entity_type="incident", entity_id=str(incident_id),
            details={"description": inc["description"]},
        )
    return {"id": incident_id, "status": "dismissed"}


@app.post("/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: int):
    with get_connection() as conn:
        inc = _load_incident(conn, incident_id)
        if inc["status"] != "verified":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot resolve an incident that is {inc['status']}",
            )
        conn.execute(
            "UPDATE incidents SET status = 'resolved', resolved_at = now() "
            "WHERE id = %s", (incident_id,),
        )
        road_reopened = False
        if inc["road_edge_id"] is not None:
            others = conn.execute(
                "SELECT count(*) AS n FROM incidents WHERE road_edge_id = %s "
                "AND id <> %s AND status = 'verified'",
                (inc["road_edge_id"], incident_id),
            ).fetchone()
            if others["n"] == 0:
                _set_road_for_incident(conn, inc["road_edge_id"], False)
                road_reopened = True
        append_audit(
            conn, "manager", "incident.resolved", "allowed",
            entity_type="incident", entity_id=str(incident_id),
            details={"road_edge_id": inc["road_edge_id"],
                     "road_reopened": road_reopened},
        )
    return {"id": incident_id, "status": "resolved",
            "road_reopened": road_reopened}


EVIDENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "evidence")
MAX_EVIDENCE_BYTES = 20 * 1024 * 1024  # 20 MB, photos or short videos


@app.post("/incidents/{incident_id}/evidence")
async def attach_evidence(incident_id: int, file: UploadFile):
    content = await file.read()
    if len(content) > MAX_EVIDENCE_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than 20 MB")
    if not content:
        raise HTTPException(status_code=422, detail="Empty file")
    digest = hashlib.sha256(content).hexdigest()
    safe_name = os.path.basename(file.filename or "evidence")
    with get_connection() as conn:
        inc = conn.execute(
            "SELECT status FROM incidents WHERE id = %s", (incident_id,)
        ).fetchone()
        if inc is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        if inc["status"] not in ("reported", "verified"):
            raise HTTPException(
                status_code=409,
                detail=f"Evidence cannot be attached to a {inc['status']} "
                       "incident",
            )
        row = conn.execute(
            "INSERT INTO incident_evidence (incident_id, filename, "
            "content_type, size_bytes, sha256) VALUES (%s, %s, %s, %s, %s) "
            "RETURNING id, uploaded_at",
            (incident_id, safe_name, file.content_type or "application/octet-stream",
             len(content), digest),
        ).fetchone()
        # The audit stores WHO attached WHAT hash WHEN - never the file itself.
        append_audit(
            conn, "dispatcher", "evidence.attached", "allowed",
            entity_type="incident", entity_id=str(incident_id),
            details={"evidence_id": row["id"], "filename": safe_name,
                     "size_bytes": len(content), "sha256": digest,
                     "content_type": file.content_type},
        )
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    with open(os.path.join(EVIDENCE_DIR, f"{digest}_{safe_name}"), "wb") as f:
        f.write(content)
    return {"id": row["id"], "incident_id": incident_id,
            "filename": safe_name, "size_bytes": len(content),
            "sha256": digest, "uploaded_at": row["uploaded_at"]}


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: int):
    with get_connection() as conn:
        inc = conn.execute(
            "SELECT id, description, incident_type, road_edge_id, status, "
            "reported_by, reported_at, decided_by, decided_at, resolved_at "
            "FROM incidents WHERE id = %s", (incident_id,),
        ).fetchone()
        if inc is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        evidence = conn.execute(
            "SELECT id, filename, content_type, size_bytes, sha256, "
            "uploaded_by, uploaded_at FROM incident_evidence "
            "WHERE incident_id = %s ORDER BY id", (incident_id,),
        ).fetchall()
    return {**inc, "evidence": evidence}


def _compute_route(conn, from_id, to_id, risk_weight=0.0):
    """Fetch the raw network, strip forbidden zones, find the best route.

    Returns (result_or_None, blocked_zone_names, zones, locations) so callers
    can validate, label, and explain. Forbidden zones are removed from the
    network BEFORE routing.
    """
    edges = conn.execute("SELECT * FROM road_edges").fetchall()
    locations = {
        r["id"]: r
        for r in conn.execute(
            "SELECT id, latitude, longitude FROM locations"
        ).fetchall()
    }
    zones = conn.execute(
        "SELECT * FROM zones WHERE active ORDER BY id"
    ).fetchall()
    allowed_edges, blocked = split_edges_by_zones(edges, zones, locations)
    blocked_names = sorted({z["name"] for _, z in blocked})
    result = shortest_route(allowed_edges, from_id, to_id, risk_weight)
    return result, blocked_names, zones, locations


def _no_route_error(blocked_names):
    reasons = (
        [f"Forbidden zone(s) removed roads from the network: "
         f"{', '.join(blocked_names)}."] if blocked_names else []
    )
    reasons.append("No connected open road remains between the two "
                   "locations.")
    return HTTPException(
        status_code=404,
        detail={
            "message": "No open route between these locations",
            "blocked_by_zones": blocked_names,
            "reasons": reasons,
        },
    )


def _validated_route(from_id, to_id, risk_weight=0.0):
    """Compute a route, run the independent validator, audit blocks. 404s."""
    with get_connection() as conn:
        result, blocked_names, zones, locations = _compute_route(
            conn, from_id, to_id, risk_weight)
    if result is None:
        with get_connection() as conn:
            append_audit(
                conn, "dispatcher", "route.request", "blocked",
                entity_type="route",
                details={"from": from_id, "to": to_id,
                         "blocked_by_zones": blocked_names},
            )
        raise _no_route_error(blocked_names)
    violations = validate_route(result["path"], zones, locations)
    if violations:
        with get_connection() as conn:
            append_audit(
                conn, "dispatcher", "route.request", "blocked",
                entity_type="route",
                details={"from": from_id, "to": to_id,
                         "validator": violations},
            )
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Route rejected by the independent validator",
                "violations": violations,
            },
        )
    return result, blocked_names


class RouteUseIn(BaseModel):
    from_id: int
    to_id: int
    prefer: Literal["fast", "safe"] = "safe"


@app.get("/route")
def get_route(from_id: int, to_id: int, prefer: Literal["fast", "safe"] = "fast"):
    risk_weight = 1.0 if prefer == "safe" else 0.0
    result, blocked_names = _validated_route(from_id, to_id, risk_weight)
    label = route_label(result["max_risk"])
    if label == "needs_approval":
        with get_connection() as conn:
            append_audit(
                conn, "dispatcher", "route.request", "allowed",
                entity_type="route",
                details={"from": from_id, "to": to_id,
                         "max_risk": result["max_risk"], "label": label,
                         "note": "high risk: manager approval needed before use"},
            )
    return {**result, "label": label, "blocked_by_zones": blocked_names}


@app.get("/routes")
def list_routes(from_id: int, to_id: int):
    """Up to 3 ranked alternatives, safest-weighted choice first."""
    with get_connection() as conn:
        edges = conn.execute("SELECT * FROM road_edges").fetchall()
        locations = {
            r["id"]: r
            for r in conn.execute(
                "SELECT id, latitude, longitude FROM locations"
            ).fetchall()
        }
        zones = conn.execute(
            "SELECT * FROM zones WHERE active ORDER BY id"
        ).fetchall()
        allowed_edges, blocked = split_edges_by_zones(edges, zones, locations)
        routes = ranked_routes(allowed_edges, from_id, to_id)
    blocked_names = sorted({z["name"] for _, z in blocked})
    if not routes:
        raise _no_route_error(blocked_names)
    for r in routes:
        r["label"] = route_label(r["max_risk"])
    return {
        "routes": routes,
        "recommended": routes[0],
        "blocked_by_zones": blocked_names,
    }


@app.post("/route/use-request")
def request_route_use(body: RouteUseIn, response: Response):
    """Ask to USE a route. High-risk (orange) routes need manager approval."""
    risk_weight = 1.0 if body.prefer == "safe" else 0.0
    result, blocked_names = _validated_route(
        body.from_id, body.to_id, risk_weight)
    label = route_label(result["max_risk"])
    request = {**body.model_dump(), "path": result["path"]}
    if label != "needs_approval":
        return {"status": "not_required", "label": label, "route": result}
    with get_connection() as conn:
        approval = conn.execute(
            "INSERT INTO approvals (action, entity_type, entity_id, request, "
            "warnings, requested_by) "
            "VALUES ('route.use', 'route', %s, %s, %s, 'dispatcher') "
            "RETURNING id",
            (f"{body.from_id}->{body.to_id}", Jsonb(request), Jsonb([])),
        ).fetchone()
        append_audit(
            conn, "dispatcher", "route.use_request", "pending",
            entity_type="route", entity_id=f"{body.from_id}->{body.to_id}",
            details={"approval_id": approval["id"], "request": request,
                     "route": result, "label": label,
                     "blocked_by_zones": blocked_names},
        )
    response.status_code = 202
    return {"status": "pending_approval", "approval_id": approval["id"],
            "label": label, "route": result}


class ScenarioZoneIn(BaseModel):
    name: str
    center_lat: float = Field(ge=-90, le=90)
    center_lng: float = Field(ge=-180, le=180)
    radius_km: float = Field(gt=0)


class SimulateIn(BaseModel):
    kind: Literal["close_road", "add_zone", "raise_risk"]
    edge_id: int | None = None
    risk_score: int | None = Field(default=None, ge=0, le=100)
    zone: ScenarioZoneIn | None = None


def _scenario_network(scenario: SimulateIn, edges, zones):
    """Return (edges_after, zones_after) with the hypothetical disruption
    applied to copies - the real rows are never touched."""
    edges_after = [dict(e) for e in edges]
    zones_after = list(zones)
    if scenario.kind == "close_road":
        for e in edges_after:
            if e["id"] == scenario.edge_id:
                e["status"] = "closed"
    elif scenario.kind == "raise_risk":
        for e in edges_after:
            if e["id"] == scenario.edge_id:
                e["risk_score"] = scenario.risk_score
    else:  # add_zone
        zones_after.append({
            "id": -1, "active": True,
            "name": scenario.zone.name,
            "center_lat": scenario.zone.center_lat,
            "center_lng": scenario.zone.center_lng,
            "radius_km": scenario.zone.radius_km,
        })
    return edges_after, zones_after


@app.post("/simulate/disruption")
def simulate_disruption(scenario: SimulateIn):
    if scenario.kind in ("close_road", "raise_risk") and scenario.edge_id is None:
        raise HTTPException(
            status_code=422, detail="edge_id is required for this scenario")
    if scenario.kind == "raise_risk" and scenario.risk_score is None:
        raise HTTPException(
            status_code=422, detail="risk_score is required for raise_risk")
    if scenario.kind == "add_zone" and scenario.zone is None:
        raise HTTPException(
            status_code=422, detail="zone is required for add_zone")

    with get_connection() as conn:
        edges = conn.execute("SELECT * FROM road_edges").fetchall()
        if scenario.edge_id is not None and not any(
            e["id"] == scenario.edge_id for e in edges
        ):
            raise HTTPException(status_code=404, detail="Road not found")
        locations = {
            r["id"]: r
            for r in conn.execute(
                "SELECT id, latitude, longitude FROM locations"
            ).fetchall()
        }
        zones = conn.execute(
            "SELECT * FROM zones WHERE active ORDER BY id"
        ).fetchall()
        shipments = conn.execute(
            "SELECT id, origin_id, destination_id FROM shipments ORDER BY id"
        ).fetchall()

    edges_after, zones_after = _scenario_network(scenario, edges, zones)
    base_allowed, _ = split_edges_by_zones(edges, zones, locations)
    after_allowed, _ = split_edges_by_zones(edges_after, zones_after, locations)

    def best(shipment, allowed_edges, zone_list):
        r = shortest_route(allowed_edges, shipment["origin_id"],
                           shipment["destination_id"])
        if r is None:
            return None
        if validate_route(r["path"], zone_list, locations):
            return None  # the independent validator refuses it
        r["label"] = route_label(r["max_risk"])
        return r

    impact = []
    for s in shipments:
        before = best(s, base_allowed, zones)
        after = best(s, after_allowed, zones_after)
        if before and after and before["path"] == after["path"]:
            status = "unaffected" if before["label"] == after["label"] \
                else "risk_changed"
        elif after is None and before is not None:
            status = "broken"
        elif before is None and after is not None:
            status = "restored"
        elif before is None and after is None:
            status = "still_blocked"
        else:
            status = "rerouted"
        impact.append({
            "shipment_id": s["id"],
            "origin_id": s["origin_id"],
            "destination_id": s["destination_id"],
            "before": before,
            "after": after,
            "delta_km": (round(after["distance_km"] - before["distance_km"], 2)
                         if before and after else None),
            "status": status,
        })

    summary = {
        "shipments_simulated": len(impact),
        "rerouted": sum(1 for i in impact if i["status"] == "rerouted"),
        "broken": sum(1 for i in impact if i["status"] == "broken"),
        "risk_changed": sum(1 for i in impact if i["status"] == "risk_changed"),
        "unaffected": sum(1 for i in impact if i["status"] == "unaffected"),
        "max_extra_km": max((i["delta_km"] or 0 for i in impact), default=0),
    }
    with get_connection() as conn:
        append_audit(
            conn, "dispatcher", "simulation.run", "allowed",
            entity_type="simulation", entity_id=uuid4().hex[:12],
            details={"scenario": scenario.model_dump(), "summary": summary},
        )
    return {"scenario": scenario.model_dump(), "impact": impact,
            "summary": summary}


class DatagenIn(BaseModel):
    scale: Literal["small", "medium", "large"] = "small"
    seed: int = 42
    confirm: bool = False  # required to add data when tables are not empty

SCALES = {
    "small": {"locations": 6, "vehicles": 4, "drivers": 4, "shipments": 8,
              "history": 20},
    "medium": {"locations": 12, "vehicles": 10, "drivers": 10,
               "shipments": 30, "history": 60},
    "large": {"locations": 16, "vehicles": 25, "drivers": 20,
              "shipments": 80, "history": 150},
}


@app.post("/datagen")
def run_datagen(body: DatagenIn):
    """Fill the database with a synthetic world for demos and exploration.

    Guard: refuses to mix synthetic rows into a database that already has
    data unless confirm=true is passed explicitly.
    """
    counts = SCALES[body.scale]
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT (SELECT count(*) FROM locations) AS locations, "
            "(SELECT count(*) FROM vehicles) AS vehicles").fetchone()
        if existing["locations"] or existing["vehicles"]:
            if not body.confirm:
                raise HTTPException(
                    status_code=409,
                    detail="Database is not empty. Pass confirm=true to add "
                           "synthetic data anyway.",
                )
        world = generate_world(
            body.seed, counts["locations"], counts["vehicles"],
            counts["drivers"], counts["shipments"], counts["history"])

        loc_ids = []
        for loc in world["locations"]:
            row = conn.execute(
                "INSERT INTO locations (name, location_type, latitude, "
                "longitude) VALUES (%s, %s, %s, %s) RETURNING id",
                (loc["name"], loc["location_type"], loc["latitude"],
                 loc["longitude"]),
            ).fetchone()
            loc_ids.append(row["id"])
        for road in world["roads"]:
            conn.execute(
                "INSERT INTO road_edges (from_location_id, to_location_id, "
                "distance_km, status, risk_score) VALUES (%s, %s, %s, %s, %s)",
                (loc_ids[road["from"]], loc_ids[road["to"]],
                 road["distance_km"], road["status"], road["risk_score"]),
            )
        for v in world["vehicles"]:
            conn.execute(
                "INSERT INTO vehicles (plate, vehicle_type, capacity_kg, "
                "insurance_expiry, fitness_expiry) "
                "VALUES (%s, %s, %s, %s, %s)",
                (v["plate"], v["vehicle_type"], v["capacity_kg"],
                 v["insurance_expiry"], v["fitness_expiry"]),
            )
        for d in world["drivers"]:
            conn.execute(
                "INSERT INTO drivers (full_name, licence_number, "
                "licence_expiry, hazmat_certified) VALUES (%s, %s, %s, %s)",
                (d["full_name"], d["licence_number"], d["licence_expiry"],
                 d["hazmat_certified"]),
            )
        for s in world["shipments"]:
            conn.execute(
                "INSERT INTO shipments (description, weight_kg, "
                "is_hazardous, origin_id, destination_id, "
                "required_vehicle_type) VALUES (%s, %s, %s, %s, %s, %s)",
                (s["description"], s["weight_kg"], s["is_hazardous"],
                 loc_ids[s["origin_id"]], loc_ids[s["destination_id"]],
                 s["required_vehicle_type"]),
            )
        now = datetime.now(timezone.utc)
        for h in world["history"]:
            created = now - timedelta(minutes=h["created_minutes_ago"])
            delivered = created + timedelta(
                minutes=h["travel_minutes"] + h["delay_minutes"])
            conn.execute(
                "INSERT INTO shipments (description, weight_kg, "
                "is_hazardous, origin_id, destination_id, status, created_at, "
                "delivered_at, eta_minutes, delay_minutes) "
                "VALUES (%s, %s, %s, %s, %s, 'completed', %s, %s, %s, %s)",
                (h["description"], h["weight_kg"], h["is_hazardous"],
                 loc_ids[h["origin_id"]], loc_ids[h["destination_id"]],
                 created, delivered, h["eta_minutes"], h["delay_minutes"]),
            )
        summary = {
            "scale": body.scale, "seed": body.seed,
            "locations": len(loc_ids),
            "roads": len(world["roads"]),
            "vehicles": len(world["vehicles"]),
            "drivers": len(world["drivers"]),
            "shipments": len(world["shipments"]),
            "history": len(world["history"]),
        }
        append_audit(
            conn, "datagen", "datagen.run", "allowed",
            entity_type="datagen", entity_id=uuid4().hex[:12],
            details=summary,
        )
    return summary


def _completed_samples(conn):
    """Feature/target rows from completed shipment history."""
    edges = conn.execute("SELECT * FROM road_edges").fetchall()
    shipments = conn.execute(
        "SELECT * FROM shipments WHERE status = 'completed' "
        "AND delay_minutes IS NOT NULL ORDER BY id"
    ).fetchall()
    samples = []
    for s in shipments:
        route = shortest_route(edges, s["origin_id"], s["destination_id"])
        if route is None:
            continue  # network changed since this shipment ran
        sample = route_features(route, s)
        sample["delay_minutes"] = s["delay_minutes"]
        samples.append(sample)
    return samples


class TrainIn(BaseModel):
    holdout_fraction: float = Field(default=0.2, gt=0.0, lt=0.5)


@app.post("/model/train")
def train_model(body: TrainIn):
    from random import Random
    with get_connection() as conn:
        samples = _completed_samples(conn)
    if len(samples) < MIN_HISTORY_FOR_TRAINING:
        raise HTTPException(
            status_code=409,
            detail=f"Not enough completed history: have {len(samples)}, "
                   f"need {MIN_HISTORY_FOR_TRAINING}. Generate data with "
                   "POST /datagen first.",
        )
    rng = Random(0)
    rng.shuffle(samples)
    holdout_n = max(1, int(len(samples) * body.holdout_fraction))
    holdout, train_set = samples[:holdout_n], samples[holdout_n:]
    model = train_delay_model(train_set)
    mae = delay_model_mae(model, holdout)
    save_delay_model(model)
    append_row = append_audit  # alias to keep the with-block short
    with get_connection() as conn:
        append_row(
            conn, "ai_delay_model", "model.trained", "allowed",
            entity_type="model", entity_id=str(MODEL_VERSION),
            details={"samples": len(train_set),
                     "holdout_samples": len(holdout),
                     "mae_minutes_holdout": mae,
                     "model": "xgboost", "model_version": MODEL_VERSION,
                     "features": FEATURES},
        )
    return {"samples": len(train_set), "holdout_samples": len(holdout),
            "mae_minutes_holdout": mae, "model": "xgboost",
            "model_version": MODEL_VERSION, "features": FEATURES}


class PredictIn(BaseModel):
    shipment_id: int


@app.post("/predict/eta")
def predict_eta(body: PredictIn):
    # The model only proposes: nothing is written to the shipment.
    with get_connection() as conn:
        shipment = conn.execute(
            "SELECT * FROM shipments WHERE id = %s", (body.shipment_id,)
        ).fetchone()
        if shipment is None:
            raise HTTPException(status_code=404, detail="Shipment not found")
        edges = conn.execute("SELECT * FROM road_edges").fetchall()
    route = shortest_route(edges, shipment["origin_id"],
                           shipment["destination_id"])
    if route is None:
        with get_connection() as conn:
            append_audit(
                conn, "ai_delay_model", "eta.predicted", "blocked",
                entity_type="shipment", entity_id=str(body.shipment_id),
                details={"reason": "no open route between origin and "
                                   "destination"},
            )
        raise HTTPException(
            status_code=404,
            detail="No open route between this shipment's origin and "
                   "destination, so no ETA is possible.",
        )
    if not model_exists():
        raise HTTPException(
            status_code=409,
            detail="Delay model is not trained yet. POST /model/train first.",
        )
    features = route_features(route, shipment)
    model = load_model()
    delay_pred = round(predict_delay(model, features), 1)
    travel = travel_minutes(features)
    eta = travel + round(delay_pred)
    with get_connection() as conn:
        append_audit(
            conn, "ai_delay_model", "eta.predicted", "allowed",
            entity_type="shipment", entity_id=str(body.shipment_id),
            details={"input_snapshot": {"shipment_id": body.shipment_id,
                                        "weight_kg": shipment["weight_kg"],
                                        "is_hazardous":
                                        shipment["is_hazardous"],
                                        "status": shipment["status"]},
                     "route": {"path": route["path"],
                               "distance_km": route["distance_km"],
                               "max_risk": route["max_risk"]},
                     "features": features,
                     "predicted_delay_minutes": delay_pred,
                     "travel_minutes": travel, "eta_minutes": eta,
                     "model": "xgboost", "model_version": MODEL_VERSION},
        )
    return {"shipment_id": body.shipment_id, "eta_minutes": eta,
            "travel_minutes": travel,
            "predicted_delay_minutes": delay_pred,
            "features_used": features,
            "model": {"name": "xgboost", "version": MODEL_VERSION}}


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
