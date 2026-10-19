-- PREPARED ONLY. Run separately against the Gateway database after approval.
-- Never wrap CREATE INDEX CONCURRENTLY in a transaction.
SET statement_timeout = '15min';
SET lock_timeout = '500ms';
CREATE INDEX CONCURRENTLY IF NOT EXISTS raw_backup_gateway_arrival
    ON wav_file (created_at, id) WHERE raw_deleted_at IS NULL;
