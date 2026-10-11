-- Phase 8C: driver app. Proof-of-delivery facts live on the assignment
-- (the file itself stays on disk; only its path and hash go in the DB).
ALTER TABLE assignments
    ADD COLUMN proof_path TEXT,
    ADD COLUMN proof_sha256 TEXT,
    ADD COLUMN delivery_note TEXT,
    ADD COLUMN delivered_at TIMESTAMPTZ;
