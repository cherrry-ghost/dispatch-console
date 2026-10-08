CREATE TABLE audit_log (
  id          BIGSERIAL PRIMARY KEY,   -- auto-increasing; gives the chain its order
  created_at  TIMESTAMPTZ NOT NULL,    -- Python sets it, because it goes into the hash
  actor_type  TEXT NOT NULL,           -- dispatcher, driver, manager, ai, system...
  actor_id    TEXT,
  action      TEXT NOT NULL,           -- e.g. assignment.create
  entity_type TEXT,
  entity_id   TEXT,
  outcome     TEXT NOT NULL,           -- allowed / blocked / warning
  details     JSONB NOT NULL DEFAULT '{}',  -- violations, AI input snapshot, etc.
  prev_hash   TEXT NOT NULL,
  hash        TEXT NOT NULL UNIQUE
);

CREATE FUNCTION audit_log_no_change() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_append_only
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_no_change();