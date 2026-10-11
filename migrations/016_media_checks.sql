-- Phase 11: media authenticity. Every uploaded photo (incident evidence,
-- delivery proof) is analysed and gets a stored, explainable verdict.
CREATE TABLE media_checks (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    media_kind TEXT NOT NULL CHECK (media_kind IN ('evidence', 'delivery')),
    ref_id BIGINT NOT NULL,
    filename TEXT,
    sha256 TEXT NOT NULL,
    verdict TEXT NOT NULL
        CHECK (verdict IN ('clean', 'suspicious', 'likely_fake',
                           'unverified')),
    score INT NOT NULL,
    reasons JSONB NOT NULL DEFAULT '[]',
    checked_by TEXT NOT NULL DEFAULT 'ai_media_checker',
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_media_checks_ref
    ON media_checks (media_kind, ref_id);
CREATE INDEX idx_media_checks_sha ON media_checks (sha256);
