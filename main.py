from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from audit import append_audit, verify_chain
import hashlib
import os

import networkx as nx
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
from anomalies import scan as scan_anomalies
from trust import eta_trust, planner_trust
from delay_model import (
    FEATURES, MIN_HISTORY_FOR_TRAINING, MODEL_VERSION,
    explain as explain_delay, interval_coverage, load as load_model,
    mean_absolute_error as delay_model_mae, model_exists,
    predict as predict_delay, predict_median, route_features,
    save as save_delay_model, train as train_delay_model, travel_minutes,
)
from typing import Literal
from psycopg.types.json import Jsonb
from fastapi import WebSocket, WebSocketDisconnect
from zones import point_in_zone

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class LiveManager:
    """Tracks console viewers on /ws/live and pushes new positions to them."""

    def __init__(self):
        self.viewers = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.viewers.add(ws)

    def disconnect(self, ws: WebSocket):
        self.viewers.discard(ws)

    async def broadcast(self, message: dict):
        for ws in list(self.viewers):
            try:
                await ws.send_json(message)
            except Exception:
                self.viewers.discard(ws)


live = LiveManager()
# last known in-zone state per vehicle, so zone alerts fire on transitions
_in_zone_state = {}


def _latest_positions(conn):
    return conn.execute(
        "SELECT DISTINCT ON (vehicle_id) vehicle_id, latitude, longitude, "
        "speed_kmh, source, recorded_at "
        "FROM vehicle_positions ORDER BY vehicle_id, recorded_at DESC"
    ).fetchall()


async def _ingest_position(vehicle_id, lat, lng, speed_kmh, source):
    """Validate, store, zone-check and broadcast one vehicle position."""
    with get_connection() as conn:
        vehicle = conn.execute(
            "SELECT id FROM vehicles WHERE id = %s", (vehicle_id,)
        ).fetchone()
        if vehicle is None:
            raise HTTPException(status_code=404, detail="Vehicle not found")
        conn.execute(
            "INSERT INTO vehicle_positions (vehicle_id, latitude, longitude, "
            "speed_kmh, source) VALUES (%s, %s, %s, %s, %s)",
            (vehicle_id, lat, lng, speed_kmh, source),
        )
        zones = conn.execute(
            "SELECT * FROM zones WHERE active ORDER BY id").fetchall()
    hit = next((z for z in zones if point_in_zone(lat, lng, z)), None)
    was_in = _in_zone_state.get(vehicle_id, False)
    alert = None
    if hit is not None:
        alert = {"zone_id": hit["id"], "zone_name": hit["name"],
                 "radius_km": hit["radius_km"]}
        if not was_in:
            with get_connection() as conn:
                append_audit(
                    conn, "system", "vehicle.in_zone", "warning",
                    entity_type="vehicle", entity_id=str(vehicle_id),
                    details={"zone": hit["name"],
                             "latitude": lat, "longitude": lng},
                )
    _in_zone_state[vehicle_id] = hit is not None
    await live.broadcast({
        "type": "position", "vehicle_id": vehicle_id,
        "latitude": lat, "longitude": lng,
        "speed_kmh": speed_kmh, "source": source,
        "zone_alert": alert,
    })
    return alert


class VehiclePositionIn(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    speed_kmh: float | None = Field(default=None, ge=0, le=300)
    source: str = "gps_device"


@app.post("/vehicles/{vehicle_id}/position")
async def report_position(vehicle_id: int, body: VehiclePositionIn):
    alert = await _ingest_position(vehicle_id, body.latitude, body.longitude,
                                   body.speed_kmh, body.source)
    return {"vehicle_id": vehicle_id, "zone_alert": alert,
             "recorded": True}


@app.get("/vehicles/{vehicle_id}/position")
def latest_position(vehicle_id: int):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT vehicle_id, latitude, longitude, speed_kmh, source, "
            "recorded_at FROM vehicle_positions WHERE vehicle_id = %s "
            "ORDER BY recorded_at DESC LIMIT 1", (vehicle_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404,
                            detail="No position recorded for this vehicle")
    return row


@app.get("/vehicles/positions")
def all_latest_positions():
    with get_connection() as conn:
        return _latest_positions(conn)


@app.websocket("/ws/vehicle/{vehicle_id}")
async def ws_vehicle(ws: WebSocket, vehicle_id: int):
    """Driver-app channel: the vehicle streams positions, the server answers
    each with status plus a zone alert when it enters forbidden ground."""
    await ws.accept()
    with get_connection() as conn:
        vehicle = conn.execute(
            "SELECT id FROM vehicles WHERE id = %s", (vehicle_id,)
        ).fetchone()
    if vehicle is None:
        await ws.send_json({"status": "error", "detail": "Vehicle not found"})
        await ws.close()
        return
    with get_connection() as conn:
        append_audit(
            conn, "driver", "tracking.started", "allowed",
            entity_type="vehicle", entity_id=str(vehicle_id),
            details={"channel": "websocket"},
        )
    try:
        while True:
            msg = await ws.receive_json()
            lat = msg.get("latitude")
            lng = msg.get("longitude")
            if lat is None or lng is None:
                await ws.send_json({"status": "error",
                                    "detail": "latitude and longitude "
                                              "are required"})
                continue
            alert = await _ingest_position(
                vehicle_id, float(lat), float(lng),
                msg.get("speed_kmh"), msg.get("source", "driver_app"))
            await ws.send_json({"status": "ok", "zone_alert": alert})
    except WebSocketDisconnect:
        pass
    finally:
        with get_connection() as conn:
            append_audit(
                conn, "driver", "tracking.stopped", "allowed",
                entity_type="vehicle", entity_id=str(vehicle_id),
                details={"channel": "websocket"},
            )


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    """Console channel: every vehicle position arrives here in real time."""
    await live.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keep the connection open; ignore pings
    except WebSocketDisconnect:
        pass
    finally:
        live.disconnect(ws)

class VehicleIn(BaseModel):
    plate: str
    vehicle_type: str
    capacity_kg: int
    insurance_expiry: date | None = None
    fitness_expiry: date | None = None
    home_depot_id: int | None = None
    speed_kmh: int | None = Field(default=None, ge=5, le=120)


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
            "fitness_expiry, home_depot_id, speed_kmh FROM vehicles ORDER BY id"
        ).fetchall()
    return rows


@app.post("/vehicles")
def create_vehicle(vehicle: VehicleIn):
    try:
        with get_connection() as conn:
            row = conn.execute(
                "INSERT INTO vehicles "
                "(plate, vehicle_type, capacity_kg, insurance_expiry, "
                "fitness_expiry, home_depot_id, speed_kmh) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    vehicle.plate,
                    vehicle.vehicle_type,
                    vehicle.capacity_kg,
                    vehicle.insurance_expiry,
                    vehicle.fitness_expiry,
                    vehicle.home_depot_id,
                    vehicle.speed_kmh if vehicle.speed_kmh is not None else 40,
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
    is_depot: bool = False


@app.get("/locations")
def list_locations():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, location_type, latitude, longitude, is_depot "
            "FROM locations ORDER BY id"
        ).fetchall()
    return rows


@app.post("/locations")
def create_location(location: LocationIn):
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO locations (name, location_type, latitude, longitude, "
            "is_depot) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (
                location.name,
                location.location_type,
                location.latitude,
                location.longitude,
                location.is_depot,
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
    ready_minute: int = Field(default=0, ge=0, le=1440)
    deadline_minute: int | None = Field(default=None, ge=1, le=1440)


@app.get("/shipments")
def list_shipments():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, description, weight_kg, is_hazardous, origin_id, "
            "destination_id, required_vehicle_type, status, created_at, "
            "delivered_at, eta_minutes, delay_minutes, "
            "ready_minute, deadline_minute "
            "FROM shipments ORDER BY id"
        ).fetchall()
    return rows


@app.post("/shipments")
def create_shipment(shipment: ShipmentIn):
    if shipment.origin_id == shipment.destination_id:
        raise HTTPException(
            status_code=400, detail="Origin and destination must be different"
        )
    if (shipment.deadline_minute is not None
            and shipment.deadline_minute <= shipment.ready_minute):
        raise HTTPException(
            status_code=400,
            detail="deadline_minute must be later than ready_minute",
        )
    try:
        with get_connection() as conn:
            row = conn.execute(
                "INSERT INTO shipments "
                "(description, weight_kg, is_hazardous, origin_id, destination_id, "
                "required_vehicle_type, ready_minute, deadline_minute) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id, status, created_at",
                (
                    shipment.description,
                    shipment.weight_kg,
                    shipment.is_hazardous,
                    shipment.origin_id,
                    shipment.destination_id,
                    shipment.required_vehicle_type,
                    shipment.ready_minute,
                    shipment.deadline_minute,
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


def _scenario_network(actions, edges, zones):
    """Return (edges_after, zones_after) with the hypothetical disruptions
    applied to copies - the real rows are never touched."""
    edges_after = [dict(e) for e in edges]
    zones_after = list(zones)
    for action in actions:
        if action.kind == "close_road":
            for e in edges_after:
                if e["id"] == action.edge_id:
                    e["status"] = "closed"
        elif action.kind == "raise_risk":
            for e in edges_after:
                if e["id"] == action.edge_id:
                    e["risk_score"] = action.risk_score
        else:  # add_zone
            zones_after.append({
                "id": -1, "active": True,
                "name": action.zone.name,
                "center_lat": action.zone.center_lat,
                "center_lng": action.zone.center_lng,
                "radius_km": action.zone.radius_km,
            })
    return edges_after, zones_after


def _validate_actions(actions, edges):
    """Every action must point at a real road and carry its parameters."""
    for i, action in enumerate(actions):
        if action.kind in ("close_road", "raise_risk"):
            if action.edge_id is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"Action {i}: edge_id is required for "
                           f"{action.kind}")
            if not any(e["id"] == action.edge_id for e in edges):
                raise HTTPException(
                    status_code=404,
                    detail=f"Action {i}: road {action.edge_id} not found")
            if action.kind == "raise_risk" and action.risk_score is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"Action {i}: risk_score is required for "
                           f"raise_risk")
        elif action.zone is None:
            raise HTTPException(
                status_code=422,
                detail=f"Action {i}: zone is required for add_zone")


def _network_health(edges, edges_after):
    """How much of the road network survives the scenario."""
    before_open = sum(1 for e in edges if e["status"] == "open")
    after_open = sum(1 for e in edges_after if e["status"] == "open")
    g = nx.Graph()
    for e in edges_after:
        if e["status"] == "open":
            g.add_edge(e["from_location_id"], e["to_location_id"])
    nodes = set(g.nodes)
    if not nodes:
        return {"roads_open_before": before_open,
                "roads_open_after": after_open,
                "roads_closed_by_scenario": before_open - after_open,
                "largest_connected_share": 0.0}
    biggest = max(len(c) for c in nx.connected_components(g))
    return {"roads_open_before": before_open,
            "roads_open_after": after_open,
            "roads_closed_by_scenario": before_open - after_open,
            "largest_connected_share": round(biggest / len(nodes), 3)}


def _compute_impact(edges, zones, edges_after, zones_after, locations,
                    shipments):
    """Per-shipment before/after routes plus a plain summary."""
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
    return impact, summary


def _run_scenario(actions):
    """Simulate the given actions on copies. Returns the full result dict."""
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
        shipments = conn.execute(
            "SELECT id, origin_id, destination_id FROM shipments ORDER BY id"
        ).fetchall()
    _validate_actions(actions, edges)
    edges_after, zones_after = _scenario_network(actions, edges, zones)
    impact, summary = _compute_impact(edges, zones, edges_after, zones_after,
                                      locations, shipments)
    return {"actions": [a.model_dump() for a in actions],
            "network": _network_health(edges, edges_after),
            "impact": impact, "summary": summary}


@app.post("/simulate/disruption")
def simulate_disruption(scenario: SimulateIn):
    result = _run_scenario([scenario])
    with get_connection() as conn:
        append_audit(
            conn, "dispatcher", "simulation.run", "allowed",
            entity_type="simulation", entity_id=uuid4().hex[:12],
            details={"scenario": scenario.model_dump(),
                     "summary": result["summary"]},
        )
    return {"scenario": scenario.model_dump(), "impact": result["impact"],
            "summary": result["summary"]}


class ScenarioIn(BaseModel):
    actions: list[SimulateIn] = Field(min_length=1, max_length=20)


@app.post("/simulate/scenario")
def simulate_scenario(body: ScenarioIn):
    result = _run_scenario(body.actions)
    with get_connection() as conn:
        append_audit(
            conn, "dispatcher", "simulation.scenario", "allowed",
            entity_type="simulation", entity_id=uuid4().hex[:12],
            details={"actions": result["actions"],
                     "network": result["network"],
                     "summary": result["summary"]},
        )
    return result


class PlaybookIn(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    actions: list[SimulateIn] = Field(min_length=1, max_length=20)


@app.get("/playbooks")
def list_playbooks():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, description, scenario, last_impact, tested_at, "
            "created_by, created_at FROM playbooks ORDER BY id"
        ).fetchall()
    return rows


@app.post("/playbooks")
def create_playbook(body: PlaybookIn):
    # a playbook may only be saved after its scenario passed a simulation
    result = _run_scenario(body.actions)
    try:
        with get_connection() as conn:
            row = conn.execute(
                "INSERT INTO playbooks (name, description, scenario, "
                "last_impact) VALUES (%s, %s, %s, %s) "
                "RETURNING id, tested_at",
                (body.name, body.description,
                 Jsonb(result["actions"]),
                 Jsonb({"network": result["network"],
                        "summary": result["summary"]})),
            ).fetchone()
            append_audit(
                conn, "manager", "playbook.create", "allowed",
                entity_type="playbook", entity_id=str(row["id"]),
                details={"name": body.name, "actions": result["actions"],
                         "summary": result["summary"]},
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail="A playbook with this name already exists")
    return {"id": row["id"], "name": body.name,
            "tested_at": row["tested_at"],
            "network": result["network"], "summary": result["summary"]}


def _load_playbook(conn, playbook_id):
    row = conn.execute(
        "SELECT * FROM playbooks WHERE id = %s", (playbook_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Playbook not found")
    return row


@app.post("/playbooks/{playbook_id}/test")
def test_playbook(playbook_id: int):
    """Re-run the stored scenario so the recorded impact stays honest."""
    with get_connection() as conn:
        row = _load_playbook(conn, playbook_id)
    actions = [SimulateIn(**a) for a in row["scenario"]]
    result = _run_scenario(actions)
    with get_connection() as conn:
        conn.execute(
            "UPDATE playbooks SET last_impact = %s, tested_at = now() "
            "WHERE id = %s",
            (Jsonb({"network": result["network"],
                    "summary": result["summary"]}), playbook_id),
        )
        append_audit(
            conn, "manager", "playbook.test", "allowed",
            entity_type="playbook", entity_id=str(playbook_id),
            details={"name": row["name"], "summary": result["summary"]},
        )
    return {"id": playbook_id, "name": row["name"],
            "network": result["network"], "summary": result["summary"]}


@app.post("/playbooks/{playbook_id}/apply")
def apply_playbook(playbook_id: int):
    """Apply the pre-approved response for real: close roads, add zones,
    raise risks. Idempotent - already-done steps are noted, not repeated."""
    with get_connection() as conn:
        row = _load_playbook(conn, playbook_id)
        applied, skipped = [], []
        for i, action in enumerate(row["scenario"]):
            if action["kind"] == "close_road":
                edge = conn.execute(
                    "SELECT id, status FROM road_edges WHERE id = %s",
                    (action["edge_id"],)).fetchone()
                if edge is None:
                    skipped.append({"action": i, "reason": "road vanished"})
                elif edge["status"] == "closed":
                    skipped.append({"action": i,
                                    "reason": "road already closed"})
                else:
                    conn.execute(
                        "UPDATE road_edges SET status = 'closed' WHERE id = %s",
                        (action["edge_id"],))
                    applied.append({"action": i, "kind": "close_road",
                                    "edge_id": action["edge_id"]})
            elif action["kind"] == "raise_risk":
                edge = conn.execute(
                    "SELECT id FROM road_edges WHERE id = %s",
                    (action["edge_id"],)).fetchone()
                if edge is None:
                    skipped.append({"action": i, "reason": "road vanished"})
                else:
                    conn.execute(
                        "UPDATE road_edges SET risk_score = %s WHERE id = %s",
                        (action["risk_score"], action["edge_id"]))
                    applied.append({"action": i, "kind": "raise_risk",
                                    "edge_id": action["edge_id"],
                                    "risk_score": action["risk_score"]})
            else:  # add_zone
                zone = conn.execute(
                    "INSERT INTO zones (name, center_lat, center_lng, "
                    "radius_km, active) VALUES (%s, %s, %s, %s, TRUE) "
                    "RETURNING id",
                    (action["zone"]["name"], action["zone"]["center_lat"],
                     action["zone"]["center_lng"],
                     action["zone"]["radius_km"])).fetchone()
                applied.append({"action": i, "kind": "add_zone",
                                "zone_id": zone["id"]})
        append_audit(
            conn, "manager", "playbook.apply", "allowed",
            entity_type="playbook", entity_id=str(playbook_id),
            details={"name": row["name"], "applied": applied,
                     "skipped": skipped},
        )
    return {"id": playbook_id, "name": row["name"],
            "applied": applied, "skipped": skipped}


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
                "longitude, is_depot) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (loc["name"], loc["location_type"], loc["latitude"],
                 loc["longitude"], loc["is_depot"]),
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
                "insurance_expiry, fitness_expiry, home_depot_id, speed_kmh) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (v["plate"], v["vehicle_type"], v["capacity_kg"],
                 v["insurance_expiry"], v["fitness_expiry"],
                 loc_ids[v["home_depot"]], v["speed_kmh"]),
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
                "required_vehicle_type, ready_minute, deadline_minute) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (s["description"], s["weight_kg"], s["is_hazardous"],
                 loc_ids[s["origin_id"]], loc_ids[s["destination_id"]],
                 s["required_vehicle_type"], s["ready_minute"],
                 s["deadline_minute"]),
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
    models = train_delay_model(train_set)
    mae = delay_model_mae(models, holdout)
    coverage = interval_coverage(models, holdout)
    save_delay_model(models)
    append_row = append_audit  # alias to keep the with-block short
    with get_connection() as conn:
        append_row(
            conn, "ai_delay_model", "model.trained", "allowed",
            entity_type="model", entity_id=str(MODEL_VERSION),
            details={"samples": len(train_set),
                     "holdout_samples": len(holdout),
                     "mae_minutes_holdout": mae,
                     "interval_coverage_holdout": coverage,
                     "model": "xgboost", "model_version": MODEL_VERSION,
                     "features": FEATURES},
        )
    return {"samples": len(train_set), "holdout_samples": len(holdout),
            "mae_minutes_holdout": mae,
            "interval_coverage_holdout": coverage,
            "model": "xgboost", "model_version": MODEL_VERSION,
            "features": FEATURES}


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
    models = load_model()
    delay_low, delay_pred, delay_high = predict_delay(models, features)
    delay_low, delay_pred, delay_high = (round(delay_low, 1),
                                         round(delay_pred, 1),
                                         round(delay_high, 1))
    travel = travel_minutes(features)
    eta = travel + round(delay_pred)
    eta_low = travel + round(delay_low)
    eta_high = travel + round(delay_high)
    explanation = explain_delay(models, features)
    with get_connection() as conn:
        trained = _last_training_row(conn)
    t_details = trained["details"] if trained else {}
    width = eta_high - eta_low
    trust, trust_factors = eta_trust(
        t_details.get("mae_minutes_holdout"),
        t_details.get("interval_coverage_holdout"),
        uncertainty_width=width,
        samples=t_details.get("samples"),
    )
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
                     "uncertainty": {"low_minutes": eta_low,
                                     "high_minutes": eta_high},
                     "explanation": explanation,
                     "trust_score": trust,
                     "model": "xgboost", "model_version": MODEL_VERSION},
        )
    return {"shipment_id": body.shipment_id, "eta_minutes": eta,
            "travel_minutes": travel,
            "predicted_delay_minutes": delay_pred,
            "uncertainty": {"low_minutes": eta_low,
                            "high_minutes": eta_high,
                            "confidence": 0.8},
            "explanation": explanation,
            "trust_score": {"score": trust, "factors": trust_factors},
            "features_used": features,
            "model": {"name": "xgboost", "version": MODEL_VERSION}}


def _last_training_row(conn):
    return conn.execute(
        "SELECT created_at, details FROM audit_log "
        "WHERE action = 'model.trained' ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _drift_status(models, conn, holdout_mae):
    """Compare error on the newest completed trips with training error."""
    recent = conn.execute(
        "SELECT * FROM shipments WHERE status = 'completed' "
        "AND delay_minutes IS NOT NULL "
        "ORDER BY delivered_at DESC NULLS LAST LIMIT 40"
    ).fetchall()
    edges = conn.execute("SELECT * FROM road_edges").fetchall()
    errors, checked = [], 0
    for s in recent:
        route = shortest_route(edges, s["origin_id"], s["destination_id"])
        if route is None:
            continue
        features = route_features(route, s)
        errors.append(abs(predict_median(models, features)
                          - s["delay_minutes"]))
        checked += 1
    if checked < 5 or not holdout_mae:
        return {"status": "unknown", "recent_trips": checked,
                "recent_mae_minutes": None, "ratio": None}
    recent_mae = round(sum(errors) / len(errors), 1)
    ratio = round(recent_mae / holdout_mae, 2)
    return {"status": "ok" if ratio <= 1.5 else "drift_detected",
            "recent_trips": checked, "recent_mae_minutes": recent_mae,
            "ratio": ratio}


def _model_health():
    """Full reliability report for the delay model, from the audit ledger."""
    with get_connection() as conn:
        row = _last_training_row(conn)
        if row is None or not model_exists():
            return {"trained": False, "model_version": MODEL_VERSION,
                    "trust_score": {"score": 0,
                                    "factors": {"reason": "model not "
                                                          "trained"}},
                    "how_to_train": "POST /model/train"}
        details = row["details"]
        mae = details.get("mae_minutes_holdout")
        coverage = details.get("interval_coverage_holdout")
        samples = details.get("samples")
        models = load_model()
        drift = _drift_status(models, conn, mae)
        recent_error = drift.get("recent_mae_minutes")
        effective_mae = max(mae or 0, recent_error or 0)
        score, factors = eta_trust(
            mae, coverage, drift_detected=drift["status"] == "drift_detected",
            samples=samples)
        if recent_error is not None:
            factors["recent_trips_mae_minutes"] = recent_error
        return {"trained": True, "model_version": MODEL_VERSION,
                "trained_at": row["created_at"],
                "samples": samples,
                "holdout": {"mae_minutes": mae,
                            "interval_coverage": coverage},
                "drift": drift,
                "effective_mae_minutes": round(effective_mae, 1),
                "trust_score": {"score": score, "factors": factors}}


@app.get("/model/health")
def model_health():
    health = _model_health()
    with get_connection() as conn:
        append_audit(
            conn, "system", "model.health", "allowed",
            entity_type="model", entity_id=str(health["model_version"]),
            details={"trained": health["trained"],
                     "trust_score": health["trust_score"]["score"],
                     "drift": health.get("drift", {}).get("status")},
        )
    return health


@app.get("/anomalies")
def list_anomalies():
    """Scan completed trips for delays that defy expectations, physically
    impossible travel times and contradictory records."""
    with get_connection() as conn:
        completed = conn.execute(
            "SELECT * FROM shipments WHERE status = 'completed' ORDER BY id"
        ).fetchall()
        edges = conn.execute("SELECT * FROM road_edges").fetchall()
    has_model = model_exists()
    models = load_model() if has_model else None
    predict_fn = (lambda f: predict_median(models, f)) if has_model else None
    features_by_id = {}
    for s in completed:
        route = shortest_route(edges, s["origin_id"], s["destination_id"])
        features_by_id[s["id"]] = route_features(route, s) if route else None
    findings = scan_anomalies(completed, features_by_id,
                              predict_fn=predict_fn)
    summary = {
        "checked": len(completed),
        "flagged": len(findings),
        "high": sum(1 for f in findings if f["severity"] == "high"),
        "medium": sum(1 for f in findings if f["severity"] == "medium"),
        "delay_check": "model" if has_model else "statistics",
    }
    with get_connection() as conn:
        append_audit(
            conn, "system", "anomaly.scan", "allowed",
            entity_type="anomaly_scan", entity_id=uuid4().hex[:12],
            details=summary,
        )
    return {"summary": summary, "anomalies": findings}


class PlannerRequest(BaseModel):
    shipment_ids: list[int] | None = None  # None = all pending shipments
@app.post("/planner/propose")
def propose_plan(body: PlannerRequest):
    with get_connection() as conn:
        vehicles = conn.execute(
            "SELECT * FROM vehicles ORDER BY id"
        ).fetchall()
        locations = conn.execute(
            "SELECT id, latitude, longitude, is_depot FROM locations "
            "ORDER BY id"
        ).fetchall()
        edges = conn.execute(
            "SELECT from_location_id, to_location_id, distance_km, status "
            "FROM road_edges"
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
        plan = plan_shipments(shipments, vehicles, date.today(),
                              edges=edges, locations=locations)
        trust, trust_factors = planner_trust(
            plan["summary"]["shipments_considered"],
            len(plan["skipped"]), plan["travel_model"],
            plan["summary"]["eligible_vehicles"])
        run_id = uuid4().hex[:12]
        # The planner is an AI actor: the input snapshot, the recommendation
        # and the skips are recorded before a human decides anything.
        append_audit(
            conn, "ai_planner", "plan.propose", "allowed",
            entity_type="plan", entity_id=run_id,
            details={
                "model": "ortools-cp-sat", "model_version": 2,
                "travel_model": plan["travel_model"],
                "input": {
                    "shipment_ids": [s["id"] for s in shipments],
                    "vehicle_ids": [v["id"] for v in vehicles],
                },
                "proposal": plan["proposals"],
                "vehicles": plan["vehicles"],
                "skipped": plan["skipped"],
                "summary": plan["summary"],
                "trust_score": trust,
            },
        )
    return {"plan_id": run_id, **plan,
            "trust_score": {"score": trust, "factors": trust_factors}}
