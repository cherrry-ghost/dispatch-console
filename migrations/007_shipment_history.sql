ALTER TABLE shipments ADD COLUMN delivered_at TIMESTAMPTZ;
ALTER TABLE shipments ADD COLUMN eta_minutes INTEGER;
ALTER TABLE shipments ADD COLUMN delay_minutes INTEGER;
