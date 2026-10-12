-- Phase 14: dispatcher proposes ranked route options for a shipment;
-- the driver picks one in the driver app; risky picks need a manager.
CREATE TABLE route_offers (
    id SERIAL PRIMARY KEY,
    shipment_id INTEGER NOT NULL REFERENCES shipments(id),
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    driver_id INTEGER NOT NULL REFERENCES drivers(id),
    options JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'offered',
    chosen_index INTEGER,
    assignment_id INTEGER,
    offered_by TEXT NOT NULL DEFAULT 'dispatcher',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ
);
