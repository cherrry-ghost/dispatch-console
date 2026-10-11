-- Phase 8B: event pipeline and geofencing.
-- events: one row per notable occurrence (geofence crossings, road status
-- changes, assignment milestones...). The consoles subscribe to /ws/events
-- for the live feed and GET /events reads history.
CREATE TABLE events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    vehicle_id INTEGER REFERENCES vehicles(id),
    shipment_id INTEGER REFERENCES shipments(id),
    zone_id INTEGER REFERENCES zones(id),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_events_created ON events (created_at DESC);

-- zones become geofences: 'forbidden' blocks routing (old behaviour),
-- 'geofence_alert' only watches and raises events when entered/exited.
ALTER TABLE zones ADD COLUMN zone_type TEXT NOT NULL DEFAULT 'forbidden'
    CHECK (zone_type IN ('forbidden', 'geofence_alert'));
