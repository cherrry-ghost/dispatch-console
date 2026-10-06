from datetime import date
import os

import psycopg
from fastapi import FastAPI, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

app = FastAPI()


class VehicleIn(BaseModel):
    plate: str
    vehicle_type: str
    capacity_kg: int


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
            "SELECT id, plate, vehicle_type, capacity_kg FROM vehicles ORDER BY id"
        ).fetchall()
    return rows


@app.post("/vehicles")
def create_vehicle(vehicle: VehicleIn):
    try:
        with get_connection() as conn:
            row = conn.execute(
                "INSERT INTO vehicles (plate, vehicle_type, capacity_kg) "
                "VALUES (%s, %s, %s) RETURNING id",
                (vehicle.plate, vehicle.vehicle_type, vehicle.capacity_kg),
            ).fetchone()
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


@app.get("/shipments")
def list_shipments():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, description, weight_kg, is_hazardous, origin_id, "
            "destination_id, status, created_at FROM shipments ORDER BY id"
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
                "(description, weight_kg, is_hazardous, origin_id, destination_id) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id, status, created_at",
                (
                    shipment.description,
                    shipment.weight_kg,
                    shipment.is_hazardous,
                    shipment.origin_id,
                    shipment.destination_id,
                ),
            ).fetchone()
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(
            status_code=400, detail="Origin or destination location does not exist"
        )
    return {**row, **shipment.model_dump()}