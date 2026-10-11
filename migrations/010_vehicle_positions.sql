-- Phase 8A: live vehicle tracking. One row per GPS ping; the latest row per
-- vehicle is the vehicle's current position on the map.
CREATE TABLE vehicle_positions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    speed_kmh REAL,
    source TEXT NOT NULL DEFAULT 'driver_app',
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_vehicle_positions_latest
    ON vehicle_positions (vehicle_id, recorded_at DESC);
