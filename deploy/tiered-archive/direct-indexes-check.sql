-- Read-only catalogue check, before CIC and again after every definition exists.
-- Run standalone with psql -X -v ON_ERROR_STOP=1 -f direct-indexes-check.sql.
\set ON_ERROR_STOP on
\if :{?direct_index_require_all}
\else
\set direct_index_require_all true
\endif
SET search_path = pg_catalog, public;
SELECT set_config('greenmind.direct_index_require_all', :'direct_index_require_all', false);

DO $guard$
DECLARE
  expected record;
  table_oid oid;
  index_oid oid;
  actual record;
  key_definitions text[];
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_proc
    WHERE oid = 'pg_catalog.to_timestamp(double precision)'::regprocedure
      AND provolatile = 'i'
  ) THEN
    RAISE EXCEPTION 'Direct expression indexes require immutable native to_timestamp(float8)';
  END IF;
  FOR expected IN
    SELECT * FROM (VALUES
      ('direct_chunk', 'tiered_direct_chunk_scan', 'received_at', 'double precision',
       ARRAY['to_timestamp(received_at)', 'id']::text[]),
      ('direct_revision', 'tiered_direct_revision_scan', 'verified_at', 'double precision',
       ARRAY['to_timestamp(verified_at)', 'segment_id', 'revision']::text[]),
      ('direct_visual_point', 'tiered_direct_visual_point_scan', 'bucket', 'bigint',
       ARRAY['to_timestamp(bucket::double precision)', 'segment_id', 'channel', 'bucket']::text[])
    ) AS definitions(table_name, index_name, time_column, time_type, keys)
  LOOP
    table_oid := to_regclass(format('public.%I', expected.table_name));
    IF table_oid IS NULL OR NOT EXISTS (
      SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_attribute a ON a.attrelid = c.oid
      WHERE c.oid = table_oid AND c.relkind = 'r' AND n.nspname = 'public'
        AND a.attname = expected.time_column AND NOT a.attisdropped
        AND a.atttypid = expected.time_type::regtype
    ) THEN
      RAISE EXCEPTION 'Direct table/type contract changed: %', expected.table_name;
    END IF;
    IF EXISTS (
      SELECT 1 FROM unnest(expected.keys[2:cardinality(expected.keys)]) AS keys(column_name)
      WHERE NOT EXISTS (
        SELECT 1 FROM pg_attribute a WHERE a.attrelid = table_oid
          AND a.attname = keys.column_name AND a.attnum > 0 AND NOT a.attisdropped
      )
    ) THEN
      RAISE EXCEPTION 'Direct key column missing: %', expected.table_name;
    END IF;
    index_oid := to_regclass(format('public.%I', expected.index_name));
    IF index_oid IS NULL THEN
      IF current_setting('greenmind.direct_index_require_all')::boolean THEN
        RAISE EXCEPTION 'Required Direct expression index missing: %', expected.index_name;
      END IF;
      CONTINUE;
    END IF;
    SELECT x.*, am.amname INTO actual
    FROM pg_index x JOIN pg_class c ON c.oid = x.indexrelid
    JOIN pg_am am ON am.oid = c.relam
    WHERE x.indexrelid = index_oid;
    IF NOT FOUND OR actual.indrelid <> table_oid OR actual.amname <> 'btree'
       OR NOT actual.indisvalid OR NOT actual.indisready OR NOT actual.indislive
       OR actual.indisunique OR actual.indisexclusion OR actual.indpred IS NOT NULL
       OR actual.indnkeyatts <> cardinality(expected.keys)
       OR actual.indnatts <> cardinality(expected.keys)
       OR actual.indkey[0] <> 0 THEN
      RAISE EXCEPTION 'Unsafe or mismatched Direct index: %', expected.index_name;
    END IF;
    SELECT array_agg(pg_get_indexdef(index_oid, position, true) ORDER BY position)
      INTO key_definitions FROM generate_series(1, actual.indnatts) AS position;
    IF key_definitions IS DISTINCT FROM expected.keys THEN
      RAISE EXCEPTION 'Direct index expression/order differs: %', expected.index_name;
    END IF;
  END LOOP;
END
$guard$;
