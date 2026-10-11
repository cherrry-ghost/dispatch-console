-- Phase 5C: time windows, multi-depot starts, mixed-fleet speeds.
-- Times are "operation minutes" within one day: 0 = midnight, 1440 = next midnight.
ALTER TABLE locations ADD COLUMN is_depot BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE vehicles ADD COLUMN home_depot_id INTEGER REFERENCES locations(id);
ALTER TABLE vehicles ADD COLUMN speed_kmh INTEGER NOT NULL DEFAULT 40
    CHECK (speed_kmh BETWEEN 5 AND 120);
ALTER TABLE shipments ADD COLUMN ready_minute INTEGER NOT NULL DEFAULT 0
    CHECK (ready_minute BETWEEN 0 AND 1440);
ALTER TABLE shipments ADD COLUMN deadline_minute INTEGER
    CHECK (deadline_minute IS NULL OR deadline_minute BETWEEN 0 AND 1440);

-- Existing data keeps working: the oldest location becomes the depot and
-- every vehicle without a home starts there.
UPDATE locations SET is_depot = TRUE
WHERE id = (SELECT MIN(id) FROM locations);
UPDATE vehicles SET home_depot_id = (SELECT MIN(id) FROM locations);
