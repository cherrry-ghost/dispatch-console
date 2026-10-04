import sqlite3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()


class VehicleIn(BaseModel):
    plate: str
    vehicle_type: str
    capacity_kg: int


def get_connection():
    conn = sqlite3.connect("dispatch.db")
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/")
def home():
    return {"message": "Dispatch console is running"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/vehicles")
def list_vehicles():
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, plate, vehicle_type, capacity_kg FROM vehicles"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@app.post("/vehicles")
def create_vehicle(vehicle: VehicleIn):
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO vehicles (plate, vehicle_type, capacity_kg) VALUES (?, ?, ?)",
            (vehicle.plate, vehicle.vehicle_type, vehicle.capacity_kg),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=409, detail="A vehicle with this plate already exists")
    new_id = cur.lastrowid
    conn.close()
    return {"id": new_id, **vehicle.model_dump()}