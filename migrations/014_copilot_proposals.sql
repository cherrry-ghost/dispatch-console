-- Phase 9: the Decision Ledger. Every copilot suggestion is stored with its
-- input snapshot, guardrail results and confidence, and stays replayable
-- with the human decision that followed it.
CREATE TABLE copilot_proposals (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question TEXT NOT NULL,
    intent TEXT NOT NULL,
    confidence REAL NOT NULL,
    answer TEXT NOT NULL,
    proposal JSONB,
    guardrail_results JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'accepted', 'rejected')),
    decided_by TEXT,
    decision_note TEXT,
    decided_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_copilot_proposals_status
    ON copilot_proposals (status, created_at DESC);
