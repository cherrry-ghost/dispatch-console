-- Phase 6D: contingency playbooks. A playbook stores a tested multi-failure
-- scenario and the impact measured when it was last simulated, so the
-- response can be applied for real in seconds when the scenario happens.
CREATE TABLE playbooks (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    scenario JSONB NOT NULL,      -- the list of actions (close roads, zone, risk)
    last_impact JSONB NOT NULL,   -- simulation summary at test time
    tested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT NOT NULL DEFAULT 'manager',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
