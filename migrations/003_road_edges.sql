CREATE TABLE road_edges (
    id SERIAL PRIMARY KEY,
    from_location_id INTEGER NOT NULL REFERENCES locations(id),
    to_location_id INTEGER NOT NULL REFERENCES locations(id),
    distance_km NUMERIC(8,2) NOT NULL CHECK (distance_km > 0),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
    risk_score INTEGER NOT NULL DEFAULT 0 CHECK (risk_score BETWEEN 0 AND 100),
    CHECK (from_location_id < to_location_id),
    UNIQUE (from_location_id, to_location_id)
);
