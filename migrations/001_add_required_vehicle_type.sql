-- Migration 001: shipments can say which vehicle type they need.
-- NULL means "any vehicle type is acceptable".
ALTER TABLE shipments ADD COLUMN required_vehicle_type TEXT;