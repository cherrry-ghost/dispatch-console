CREATE TABLE incidents (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    description TEXT NOT NULL,
    incident_type TEXT NOT NULL,
    road_edge_id BIGINT REFERENCES road_edges (id),
    status TEXT NOT NULL DEFAULT 'reported'
        CHECK (status IN ('reported', 'verified', 'dismissed', 'resolved')),
    reported_by TEXT NOT NULL DEFAULT 'dispatcher',
    reported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_by TEXT,
    decided_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ
);

CREATE TABLE incident_evidence (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id BIGINT NOT NULL REFERENCES incidents (id),
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    sha256 TEXT NOT NULL,
    uploaded_by TEXT NOT NULL DEFAULT 'dispatcher',
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
