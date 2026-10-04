CREATE TABLE vehicles (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plate TEXT NOT NULL UNIQUE,
    vehicle_type TEXT NOT NULL,
    capacity_kg INTEGER NOT NULL CHECK (capacity_kg > 0),
    insurance_expiry DATE,
    fitness_expiry DATE
);

CREATE TABLE drivers (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name TEXT NOT NULL,
    licence_number TEXT NOT NULL UNIQUE,
    licence_expiry DATE NOT NULL,
    hazmat_certified BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE locations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL,
    location_type TEXT NOT NULL,
    latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180)
);

CREATE TABLE shipments (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    description TEXT NOT NULL,
    weight_kg INTEGER NOT NULL CHECK (weight_kg > 0),
    is_hazardous BOOLEAN NOT NULL DEFAULT FALSE,
    origin_id BIGINT NOT NULL REFERENCES locations (id),
    destination_id BIGINT NOT NULL REFERENCES locations (id),
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (origin_id <> destination_id)
);

CREATE TABLE assignments (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shipment_id BIGINT NOT NULL REFERENCES shipments (id),
    vehicle_id BIGINT NOT NULL REFERENCES vehicles (id),
    driver_id BIGINT NOT NULL REFERENCES drivers (id),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    reason TEXT NOT NULL,
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE UNIQUE INDEX one_active_assignment_per_shipment
    ON assignments (shipment_id)
    WHERE ended_at IS NULL;