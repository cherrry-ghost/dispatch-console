import sqlite3
from fastapi import FastAPI

app = FastAPI()


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