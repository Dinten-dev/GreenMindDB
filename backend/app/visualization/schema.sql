CREATE TABLE IF NOT EXISTS visual_chunk (
    chunk_name text PRIMARY KEY,
    range_start timestamptz NOT NULL,
    range_end timestamptz NOT NULL,
    change_version bigint NOT NULL DEFAULT 0,
    archived_version bigint,
    status text NOT NULL DEFAULT 'pending',
    archive_manifest text,
    source_rows bigint,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS visual_chunk_range ON visual_chunk(range_start,range_end);
CREATE TABLE IF NOT EXISTS visual_reading (
    sensor_id uuid NOT NULL REFERENCES sensor(id) ON DELETE CASCADE,
    kind text NOT NULL,
    bucket timestamptz NOT NULL,
    seconds integer NOT NULL CHECK(seconds IN (60,600)),
    unit text NOT NULL,
    n bigint NOT NULL CHECK(n > 0),
    total double precision NOT NULL,
    total2 double precision NOT NULL,
    minimum double precision NOT NULL,
    maximum double precision NOT NULL,
    identities bytea NOT NULL,
    PRIMARY KEY(sensor_id,kind,bucket,seconds)
);
CREATE INDEX IF NOT EXISTS visual_reading_time ON visual_reading(bucket);
CREATE TABLE IF NOT EXISTS visual_wav (
    wav_id uuid PRIMARY KEY REFERENCES wav_file(id) ON DELETE CASCADE,
    source_sha256 text NOT NULL,
    seconds integer NOT NULL,
    status text NOT NULL,
    error text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS visual_wave (
    wav_id uuid NOT NULL REFERENCES visual_wav(wav_id) ON DELETE CASCADE,
    sensor_id uuid NOT NULL REFERENCES sensor(id) ON DELETE CASCADE,
    bucket timestamptz NOT NULL,
    seconds integer NOT NULL CHECK(seconds IN (60,600)),
    n bigint NOT NULL,
    total double precision NOT NULL,
    total2 double precision NOT NULL,
    minimum double precision NOT NULL,
    maximum double precision NOT NULL,
    duration double precision NOT NULL,
    PRIMARY KEY(wav_id,bucket,seconds)
);
CREATE INDEX IF NOT EXISTS visual_wave_sensor_time ON visual_wave(sensor_id,bucket);
CREATE TABLE IF NOT EXISTS visual_worker (
    name text PRIMARY KEY,
    status text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now()
);
-- One inexpensive comparison for current readings. Only tracked historical
-- chunks acquire a counter lock. The marker detects writes during a snapshot.
CREATE OR REPLACE FUNCTION visual_track_historical_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE stamp timestamptz;
BEGIN
    stamp := CASE WHEN TG_OP = 'DELETE' THEN OLD.timestamp ELSE NEW.timestamp END;
    IF stamp < now() - interval '24 hours' THEN
        UPDATE visual_chunk SET change_version = txid_current(), updated_at=now()
        WHERE stamp >= range_start AND stamp < range_end
          AND change_version <> txid_current();
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.timestamp <> NEW.timestamp THEN
        UPDATE visual_chunk SET change_version = txid_current(), updated_at=now()
        WHERE OLD.timestamp >= range_start AND OLD.timestamp < range_end
          AND change_version <> txid_current();
    END IF;
    RETURN NULL;
END $$;
