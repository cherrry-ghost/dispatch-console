CREATE TABLE approvals (
  id            BIGSERIAL PRIMARY KEY,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  action        TEXT NOT NULL,          -- what is waiting, e.g. assignment.create
  entity_type   TEXT NOT NULL,
  entity_id     TEXT NOT NULL,
  request       JSONB NOT NULL,         -- the original request, so we can apply it later
  warnings      JSONB NOT NULL,         -- the warn rules that triggered the approval
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected')),
  requested_by  TEXT NOT NULL,
  decided_by    TEXT,
  decided_at    TIMESTAMPTZ,
  decision_note TEXT
);