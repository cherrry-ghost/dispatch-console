-- Phase 8D: incident reporting and customer notifications.
-- Shipments learn who to tell; notifications remembers what was told.
ALTER TABLE shipments
    ADD COLUMN customer_name TEXT,
    ADD COLUMN customer_phone TEXT;

CREATE TABLE notifications (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shipment_id BIGINT NOT NULL REFERENCES shipments(id),
    kind TEXT NOT NULL,
    recipient TEXT NOT NULL,
    message TEXT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_notifications_shipment ON notifications (shipment_id, sent_at DESC);
