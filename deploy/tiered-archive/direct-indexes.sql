-- psql -X -v ON_ERROR_STOP=1 -f direct-indexes.sql; never wrap in a transaction.
-- Review source headroom first. A same-name numeric/partial/invalid index aborts;
-- this script never drops, replaces or silently accepts an existing definition.
\set ON_ERROR_STOP on
\set direct_index_require_all false
\ir direct-indexes-check.sql

CREATE INDEX CONCURRENTLY IF NOT EXISTS tiered_direct_chunk_scan ON public.direct_chunk (pg_catalog.to_timestamp(received_at), id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS tiered_direct_revision_scan ON public.direct_revision (pg_catalog.to_timestamp(verified_at), segment_id, revision);
CREATE INDEX CONCURRENTLY IF NOT EXISTS tiered_direct_visual_point_scan ON public.direct_visual_point (pg_catalog.to_timestamp(bucket::double precision), segment_id, channel, bucket);

\set direct_index_require_all true
\ir direct-indexes-check.sql
