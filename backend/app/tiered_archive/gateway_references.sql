-- Additive, compact references remain local for idempotency after eviction.
-- Install under an administrator-owned schema before accepting SQL retention.
CREATE TABLE IF NOT EXISTS public.tiered_reading_identity (
    archive_candidate varchar(64) NOT NULL,
    sensor_id uuid NOT NULL,
    kind text NOT NULL,
    source_day date NOT NULL,
    identities bigint[] NOT NULL,
    PRIMARY KEY(archive_candidate,sensor_id,kind,source_day)
);
CREATE INDEX IF NOT EXISTS tiered_reading_identity_lookup
    ON public.tiered_reading_identity(sensor_id,kind,source_day);
CREATE OR REPLACE FUNCTION public.tiered_reading_lock_key(sensor uuid,kind text,day date)
RETURNS bigint LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
    SELECT hashtextextended(sensor::text||'|'||kind||'|'||(day-date '1970-01-01')::text,20261026)
$$;
CREATE OR REPLACE FUNCTION public.tiered_reading_deduplicate() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE day date;
BEGIN
    IF NEW.timestamp < now()-interval '24 hours' THEN
        day := (NEW.timestamp AT TIME ZONE 'UTC')::date;
        -- Shares a transaction fence with the exact archival deletion. A retry
        -- waiting behind eviction sees its committed identity before inserting.
        PERFORM pg_advisory_xact_lock(public.tiered_reading_lock_key(NEW.sensor_id,NEW.kind,day));
        IF EXISTS (SELECT 1 FROM public.tiered_reading_identity a
            WHERE a.sensor_id=NEW.sensor_id AND a.kind=NEW.kind AND a.source_day=day
            AND (extract(epoch FROM NEW.timestamp)*1000000)::bigint=ANY(a.identities)) THEN
            RETURN NULL;
        END IF;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS tiered_reading_deduplicate ON public.sensor_reading;
CREATE TRIGGER tiered_reading_deduplicate BEFORE INSERT ON public.sensor_reading
    FOR EACH ROW EXECUTE FUNCTION public.tiered_reading_deduplicate();
CREATE TABLE IF NOT EXISTS public.tiered_visual_reference (
    sensor_id uuid NOT NULL,
    kind text NOT NULL,
    bucket timestamptz NOT NULL,
    seconds integer NOT NULL CHECK(seconds=600),
    unit text NOT NULL,
    n bigint NOT NULL,
    total double precision NOT NULL,
    total2 double precision NOT NULL,
    minimum double precision NOT NULL,
    maximum double precision NOT NULL,
    identities bytea NOT NULL,
    archive_candidate varchar(64) NOT NULL,
    PRIMARY KEY(sensor_id,kind,bucket,seconds)
);
CREATE TABLE IF NOT EXISTS public.tiered_wave_reference (
    wav_id uuid NOT NULL,
    bucket timestamptz NOT NULL,
    seconds integer NOT NULL CHECK(seconds=600),
    n bigint NOT NULL,
    total double precision NOT NULL,
    total2 double precision NOT NULL,
    duration double precision NOT NULL,
    archive_candidate varchar(64) NOT NULL,
    PRIMARY KEY(wav_id,bucket,seconds)
);
