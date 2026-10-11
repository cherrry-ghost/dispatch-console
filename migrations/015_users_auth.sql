-- Phase 10: security. Named users with roles for the console; a PIN for
-- drivers. Passwords and PINs are stored as PBKDF2-SHA256 hashes, never
-- plaintext. Default seeds (CHANGE IN PRODUCTION):
--   admin / admin123, dispatcher / dispatch123, manager / manager123
CREATE TABLE users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'dispatcher', 'manager')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE drivers ADD COLUMN pin_hash TEXT;

INSERT INTO users (username, password_hash, role) VALUES
    ('admin', 'pbkdf2$600000$7104a1ab6c370e044d7f221f7ee7ccc2$9cbd9b7069350a5e49034c2513a499ae405d550a4ac85fa7ecd93ee0527e06ed', 'admin'),
    ('dispatcher', 'pbkdf2$600000$abef477a3637e285248336d0de05af39$40e3f0f135e4c5fbc6a47b60ed75376710573ba56f97bb5267bd873bedffb743', 'dispatcher'),
    ('manager', 'pbkdf2$600000$2b397d5feaec823b0db2e4722ef1b11e$fdcab3b48bb9c546abdace71caebd6f9ceb75b6aa39fbdb72eafe1ef292da752', 'manager')
ON CONFLICT (username) DO NOTHING;

-- every driver seeded so far gets the default PIN 1234 (hashed at runtime
-- by the app if NULL); datagen hashes new drivers' PINs from now on
