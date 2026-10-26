-- Install only in the dedicated Direct database. No change to ingest tables.
CREATE TABLE IF NOT EXISTS direct_recording_locator (
    segment_id varchar(36) NOT NULL REFERENCES direct_segment(id),
    revision integer NOT NULL,
    manifest_sha256 varchar(64) NOT NULL,
    manifest jsonb NOT NULL,
    archive_candidate varchar(64) NOT NULL,
    archived_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(segment_id,revision)
);
