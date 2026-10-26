-- Execute separately, outside a transaction, after reviewing source headroom.
-- No data changes and no automatic startup migration.
-- sensor_reading already has its timestamp/sensor/kind primary index.
-- Timescale hypertables do not support CREATE INDEX CONCURRENTLY.
CREATE INDEX CONCURRENTLY IF NOT EXISTS tiered_visual_reading_scan ON visual_reading(bucket,sensor_id,kind,seconds);
CREATE INDEX CONCURRENTLY IF NOT EXISTS tiered_visual_wave_scan ON visual_wave(bucket,wav_id,seconds);
CREATE INDEX CONCURRENTLY IF NOT EXISTS tiered_wav_feature_scan ON wav_feature(updated_at,id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS tiered_wav_feature_version_scan ON wav_feature_version(created_at,id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS tiered_ingest_log_scan ON ingest_log(created_at,measurement_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS tiered_gateway_state_report_scan ON gateway_state_report(reported_at,id);
