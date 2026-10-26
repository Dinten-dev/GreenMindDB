-- Execute separately, outside a transaction, after reviewing source headroom.
CREATE INDEX CONCURRENTLY IF NOT EXISTS tiered_direct_chunk_scan ON direct_chunk(received_at,id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS tiered_direct_revision_scan ON direct_revision(verified_at,segment_id,revision);
CREATE INDEX CONCURRENTLY IF NOT EXISTS tiered_direct_visual_point_scan ON direct_visual_point(bucket,segment_id,channel);
