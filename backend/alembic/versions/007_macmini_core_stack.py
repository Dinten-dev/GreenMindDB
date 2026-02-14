"""macmini_core_stack

Revision ID: 007
Revises: 006
Create Date: 2026-02-14 15:10:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS greenhouse (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL,
            location TEXT,
            timezone TEXT NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS zone (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            greenhouse_id UUID NOT NULL REFERENCES greenhouse(id) ON DELETE CASCADE,
            name TEXT NOT NULL
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS plant (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            zone_id UUID NOT NULL REFERENCES zone(id) ON DELETE CASCADE,
            species TEXT NOT NULL,
            cultivar TEXT,
            planted_at TIMESTAMPTZ,
            tags JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS device (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            greenhouse_id UUID NOT NULL REFERENCES greenhouse(id) ON DELETE CASCADE,
            serial TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL,
            fw_version TEXT,
            last_seen TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'unknown'
        )
        """
    )

    # Backward-compatible upgrades when a legacy `device` table already exists.
    op.execute(
        """
        ALTER TABLE device
            ADD COLUMN IF NOT EXISTS greenhouse_id UUID REFERENCES greenhouse(id) ON DELETE CASCADE,
            ADD COLUMN IF NOT EXISTS serial TEXT,
            ADD COLUMN IF NOT EXISTS type TEXT,
            ADD COLUMN IF NOT EXISTS fw_version TEXT,
            ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS status TEXT
        """
    )
    op.execute(
        """
        UPDATE device
        SET
            serial = COALESCE(serial, 'legacy-' || id::text),
            type = COALESCE(type, device_type, 'unknown'),
            fw_version = COALESCE(fw_version, 'unknown'),
            last_seen = COALESCE(last_seen, created_at),
            status = COALESCE(status, CASE WHEN is_active IS TRUE THEN 'online' ELSE 'unknown' END)
        """
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_device_serial ON device(serial)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sensor (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            device_id UUID NOT NULL REFERENCES device(id) ON DELETE CASCADE,
            plant_id UUID REFERENCES plant(id) ON DELETE SET NULL,
            zone_id UUID REFERENCES zone(id) ON DELETE SET NULL,
            kind TEXT NOT NULL,
            unit TEXT NOT NULL,
            calibration JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS plant_signal_1hz (
            time TIMESTAMPTZ NOT NULL,
            plant_id UUID NOT NULL REFERENCES plant(id) ON DELETE CASCADE,
            sensor_id UUID NOT NULL REFERENCES sensor(id) ON DELETE CASCADE,
            value_uv DOUBLE PRECISION NOT NULL,
            quality SMALLINT NOT NULL DEFAULT 0,
            meta JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (time, plant_id, sensor_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS env_measurement (
            time TIMESTAMPTZ NOT NULL,
            sensor_id UUID NOT NULL REFERENCES sensor(id) ON DELETE CASCADE,
            value DOUBLE PRECISION NOT NULL,
            quality SMALLINT NOT NULL DEFAULT 0,
            meta JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (time, sensor_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS event_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            greenhouse_id UUID NOT NULL REFERENCES greenhouse(id) ON DELETE CASCADE,
            time TIMESTAMPTZ NOT NULL,
            type TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_by TEXT,
            source_device_id UUID REFERENCES device(id) ON DELETE SET NULL
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS object_meta (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            time TIMESTAMPTZ NOT NULL,
            greenhouse_id UUID NOT NULL REFERENCES greenhouse(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            storage_key TEXT NOT NULL,
            content_type TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes BIGINT NOT NULL,
            plant_id UUID REFERENCES plant(id) ON DELETE SET NULL,
            zone_id UUID REFERENCES zone(id) ON DELETE SET NULL
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ingest_log (
            request_id UUID PRIMARY KEY,
            received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            endpoint TEXT NOT NULL,
            source TEXT,
            status TEXT NOT NULL,
            details JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS export_job (
            id UUID PRIMARY KEY,
            greenhouse_id UUID NOT NULL REFERENCES greenhouse(id) ON DELETE CASCADE,
            plant_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            from_time TIMESTAMPTZ NOT NULL,
            to_time TIMESTAMPTZ NOT NULL,
            include_env BOOLEAN NOT NULL DEFAULT TRUE,
            include_events BOOLEAN NOT NULL DEFAULT TRUE,
            resample TEXT NOT NULL DEFAULT 'raw',
            status TEXT NOT NULL,
            storage_key TEXT,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ
        )
        """
    )

    op.execute("SELECT create_hypertable('plant_signal_1hz', 'time', if_not_exists => TRUE)")
    op.execute("SELECT create_hypertable('env_measurement', 'time', if_not_exists => TRUE)")

    op.execute("CREATE INDEX IF NOT EXISTS idx_plant_signal_sensor_time ON plant_signal_1hz(sensor_id, time DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_plant_signal_plant_time ON plant_signal_1hz(plant_id, time DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_env_measurement_sensor_time ON env_measurement(sensor_id, time DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_event_greenhouse_time ON event_log(greenhouse_id, time DESC)")

    op.execute(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS plant_signal_1hz_1m
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket(INTERVAL '1 minute', time) AS bucket,
            plant_id,
            sensor_id,
            avg(value_uv) AS value_avg,
            min(value_uv) AS value_min,
            max(value_uv) AS value_max,
            count(*) AS sample_count
        FROM plant_signal_1hz
        GROUP BY bucket, plant_id, sensor_id
        WITH NO DATA
        """
    )
    op.execute(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS plant_signal_1hz_15m
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket(INTERVAL '15 minute', time) AS bucket,
            plant_id,
            sensor_id,
            avg(value_uv) AS value_avg,
            min(value_uv) AS value_min,
            max(value_uv) AS value_max,
            count(*) AS sample_count
        FROM plant_signal_1hz
        GROUP BY bucket, plant_id, sensor_id
        WITH NO DATA
        """
    )

    op.execute(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS env_measurement_1m
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket(INTERVAL '1 minute', time) AS bucket,
            sensor_id,
            avg(value) AS value_avg,
            min(value) AS value_min,
            max(value) AS value_max,
            count(*) AS sample_count
        FROM env_measurement
        GROUP BY bucket, sensor_id
        WITH NO DATA
        """
    )
    op.execute(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS env_measurement_15m
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket(INTERVAL '15 minute', time) AS bucket,
            sensor_id,
            avg(value) AS value_avg,
            min(value) AS value_min,
            max(value) AS value_max,
            count(*) AS sample_count
        FROM env_measurement
        GROUP BY bucket, sensor_id
        WITH NO DATA
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS idx_plant_signal_1m_bucket ON plant_signal_1hz_1m(bucket DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_plant_signal_15m_bucket ON plant_signal_1hz_15m(bucket DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_env_1m_bucket ON env_measurement_1m(bucket DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_env_15m_bucket ON env_measurement_15m(bucket DESC)")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS env_measurement_15m")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS env_measurement_1m")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS plant_signal_1hz_15m")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS plant_signal_1hz_1m")

    op.execute("DROP TABLE IF EXISTS export_job")
    op.execute("DROP TABLE IF EXISTS ingest_log")
    op.execute("DROP TABLE IF EXISTS object_meta")
    op.execute("DROP TABLE IF EXISTS event_log")
    op.execute("DROP TABLE IF EXISTS env_measurement")
    op.execute("DROP TABLE IF EXISTS plant_signal_1hz")
    op.execute("DROP TABLE IF EXISTS sensor")
    op.execute("DROP TABLE IF EXISTS device")
    op.execute("DROP TABLE IF EXISTS plant")
    op.execute("DROP TABLE IF EXISTS zone")
    op.execute("DROP TABLE IF EXISTS greenhouse")
