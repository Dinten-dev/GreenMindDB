-- PREPARED ONLY. Run separately against the Direct database after approval.
SET statement_timeout = '15min';
SET lock_timeout = '500ms';
CREATE INDEX CONCURRENTLY IF NOT EXISTS raw_backup_direct_arrival
    ON direct_revision (verified_at, segment_id, revision) WHERE raw_deleted_at IS NULL;
