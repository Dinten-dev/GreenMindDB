"""GreenMindDB v2 – complete schema rebuild.

Revision ID: 0001_greenminddb_v2
Create Date: 2026-02-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID, TIMESTAMP


revision = "0001_greenminddb_v2"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Extensions ────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")
    op.execute("CREATE EXTENSION IF NOT EXISTS \"pgcrypto\"")
    op.execute("CREATE EXTENSION IF NOT EXISTS \"timescaledb\"")

    # ── Enums are created automatically by sa.Enum() in create_table below

    # ── Master data ───────────────────────────────────────────
    op.create_table(
        "greenhouse",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("location", sa.String(500)),
        sa.Column("timezone", sa.String(50), nullable=False, server_default="UTC"),
    )

    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.Enum("admin","operator","research","viewer", name="user_role"), nullable=False),
        sa.Column("greenhouse_id", UUID(as_uuid=True), sa.ForeignKey("greenhouse.id"), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "refresh_token",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_refresh_token_user_id", "refresh_token", ["user_id"])

    op.create_table(
        "zone",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("greenhouse_id", UUID(as_uuid=True), sa.ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
    )
    op.create_index("ix_zone_greenhouse_id", "zone", ["greenhouse_id"])

    op.create_table(
        "plant",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("zone_id", UUID(as_uuid=True), sa.ForeignKey("zone.id", ondelete="CASCADE"), nullable=False),
        sa.Column("species", sa.String(200), nullable=False),
        sa.Column("cultivar", sa.String(200)),
        sa.Column("planted_at", sa.DateTime(timezone=True)),
        sa.Column("tags", JSONB, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_plant_zone_id", "plant", ["zone_id"])

    op.create_table(
        "device",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("greenhouse_id", UUID(as_uuid=True), sa.ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False),
        sa.Column("serial", sa.String(100), nullable=False, unique=True),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("fw_version", sa.String(50)),
        sa.Column("last_seen", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default="offline"),
    )
    op.create_index("ix_device_greenhouse_id", "device", ["greenhouse_id"])

    op.create_table(
        "sensor",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("device_id", UUID(as_uuid=True), sa.ForeignKey("device.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plant.id", ondelete="SET NULL")),
        sa.Column("zone_id", UUID(as_uuid=True), sa.ForeignKey("zone.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("calibration", JSONB, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_sensor_device_id", "sensor", ["device_id"])
    op.create_index("ix_sensor_plant_id", "sensor", ["plant_id"])
    op.create_index("ix_sensor_zone_id", "sensor", ["zone_id"])

    # ── Timeseries (raw SQL for hypertables) ──────────────────
    op.execute("""
        CREATE TABLE plant_signal_1hz (
            time        TIMESTAMPTZ NOT NULL,
            greenhouse_id UUID      NOT NULL,
            plant_id    UUID        NOT NULL,
            sensor_id   UUID        NOT NULL,
            value_uv    DOUBLE PRECISION NOT NULL,
            quality     SMALLINT    DEFAULT 0,
            meta        JSONB       DEFAULT '{}'::jsonb
        )
    """)
    op.execute("SELECT create_hypertable('plant_signal_1hz', 'time')")
    op.execute("CREATE INDEX ix_signal_sensor_time ON plant_signal_1hz (sensor_id, time DESC)")
    op.execute("CREATE INDEX ix_signal_plant_time ON plant_signal_1hz (plant_id, time DESC)")
    op.execute("CREATE INDEX ix_signal_gh_time ON plant_signal_1hz (greenhouse_id, time DESC)")

    op.execute("""
        CREATE TABLE env_measurement (
            time        TIMESTAMPTZ NOT NULL,
            greenhouse_id UUID      NOT NULL,
            sensor_id   UUID        NOT NULL,
            value       DOUBLE PRECISION NOT NULL,
            quality     SMALLINT    DEFAULT 0,
            meta        JSONB       DEFAULT '{}'::jsonb
        )
    """)
    op.execute("SELECT create_hypertable('env_measurement', 'time')")
    op.execute("CREATE INDEX ix_env_sensor_time ON env_measurement (sensor_id, time DESC)")
    op.execute("CREATE INDEX ix_env_gh_time ON env_measurement (greenhouse_id, time DESC)")

    # ── Continuous aggregates ─────────────────────────────────
    op.execute("""
        CREATE MATERIALIZED VIEW plant_signal_1hz_1m
        WITH (timescaledb.continuous) AS
        SELECT time_bucket('1 minute', time) AS bucket,
               greenhouse_id, plant_id, sensor_id,
               AVG(value_uv) AS value_avg,
               MIN(value_uv) AS value_min,
               MAX(value_uv) AS value_max,
               COUNT(*) AS sample_count
        FROM plant_signal_1hz
        GROUP BY bucket, greenhouse_id, plant_id, sensor_id
        WITH NO DATA
    """)

    op.execute("""
        CREATE MATERIALIZED VIEW plant_signal_1hz_15m
        WITH (timescaledb.continuous) AS
        SELECT time_bucket('15 minutes', time) AS bucket,
               greenhouse_id, plant_id, sensor_id,
               AVG(value_uv) AS value_avg,
               MIN(value_uv) AS value_min,
               MAX(value_uv) AS value_max,
               COUNT(*) AS sample_count
        FROM plant_signal_1hz
        GROUP BY bucket, greenhouse_id, plant_id, sensor_id
        WITH NO DATA
    """)

    op.execute("""
        CREATE MATERIALIZED VIEW env_measurement_1m
        WITH (timescaledb.continuous) AS
        SELECT time_bucket('1 minute', time) AS bucket,
               greenhouse_id, sensor_id,
               AVG(value) AS value_avg,
               MIN(value) AS value_min,
               MAX(value) AS value_max,
               COUNT(*) AS sample_count
        FROM env_measurement
        GROUP BY bucket, greenhouse_id, sensor_id
        WITH NO DATA
    """)

    op.execute("""
        CREATE MATERIALIZED VIEW env_measurement_15m
        WITH (timescaledb.continuous) AS
        SELECT time_bucket('15 minutes', time) AS bucket,
               greenhouse_id, sensor_id,
               AVG(value) AS value_avg,
               MIN(value) AS value_min,
               MAX(value) AS value_max,
               COUNT(*) AS sample_count
        FROM env_measurement
        GROUP BY bucket, greenhouse_id, sensor_id
        WITH NO DATA
    """)

    # ── Domain tables ─────────────────────────────────────────
    op.create_table(
        "event_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("greenhouse_id", UUID(as_uuid=True), sa.ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("payload", JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("source_device_id", UUID(as_uuid=True), sa.ForeignKey("device.id", ondelete="SET NULL")),
        sa.Column("request_id", UUID(as_uuid=True), unique=True, nullable=False),
    )
    op.create_index("ix_event_gh_time", "event_log", ["greenhouse_id", "time"])
    op.create_index("ix_event_type", "event_log", ["type"])

    op.create_table(
        "ground_truth_daily",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("greenhouse_id", UUID(as_uuid=True), sa.ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("vitality_score", sa.SmallInteger, nullable=False),
        sa.Column("growth_score", sa.SmallInteger, nullable=False),
        sa.Column("pest_score", sa.SmallInteger, nullable=False),
        sa.Column("disease_score", sa.SmallInteger, nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("greenhouse_id", "plant_id", "date", name="uq_ground_truth_plant_date"),
    )
    op.create_index("ix_gt_gh", "ground_truth_daily", ["greenhouse_id"])
    op.create_index("ix_gt_plant", "ground_truth_daily", ["plant_id"])

    op.create_table(
        "lab_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("greenhouse_id", UUID(as_uuid=True), sa.ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("analyte", sa.String(100), nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("lab_meta", JSONB, server_default=sa.text("'{}'::jsonb")),
    )

    # ── Annotation / Labels ───────────────────────────────────
    op.create_table(
        "label_schema",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("greenhouse_id", UUID(as_uuid=True), sa.ForeignKey("greenhouse.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("values", JSONB, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
    )

    op.create_table(
        "annotation",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("greenhouse_id", UUID(as_uuid=True), sa.ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sensor_id", UUID(as_uuid=True), sa.ForeignKey("sensor.id", ondelete="SET NULL")),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("label_key", sa.String(100), nullable=False),
        sa.Column("label_value", sa.String(100), nullable=False),
        sa.Column("confidence", sa.SmallInteger),
        sa.Column("notes", sa.Text),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("status", sa.Enum("draft","submitted","reviewed","rejected", name="annotation_status"), nullable=False, server_default="draft"),
    )
    op.create_index("ix_annotation_gh", "annotation", ["greenhouse_id"])
    op.create_index("ix_annotation_plant", "annotation", ["plant_id"])

    op.create_table(
        "annotation_review",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("annotation_id", UUID(as_uuid=True), sa.ForeignKey("annotation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reviewed_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decision", sa.Enum("approve","reject", name="review_decision"), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_annrev_annotation", "annotation_review", ["annotation_id"])

    # ── Media / Export ────────────────────────────────────────
    op.create_table(
        "object_meta",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("greenhouse_id", UUID(as_uuid=True), sa.ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("size_bytes", sa.BigInteger),
        sa.Column("plant_id", UUID(as_uuid=True), sa.ForeignKey("plant.id", ondelete="SET NULL")),
        sa.Column("zone_id", UUID(as_uuid=True), sa.ForeignKey("zone.id", ondelete="SET NULL")),
        sa.Column("created_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_index("ix_objmeta_gh_time", "object_meta", ["greenhouse_id", "time"])

    op.create_table(
        "export_job",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("greenhouse_id", UUID(as_uuid=True), sa.ForeignKey("greenhouse.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("params", JSONB, nullable=False),
        sa.Column("storage_key", sa.String(500)),
        sa.Column("schema_key", sa.String(500)),
        sa.Column("row_count", sa.Integer),
        sa.Column("error", JSONB),
    )

    # ── Audit ─────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("time", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("actor_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("actor_type", sa.String(20), nullable=False, server_default="USER"),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(50)),
        sa.Column("resource_id", sa.String(100)),
        sa.Column("greenhouse_id", UUID(as_uuid=True), sa.ForeignKey("greenhouse.id", ondelete="SET NULL")),
        sa.Column("ip", sa.String(50)),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("details", JSONB, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_audit_time", "audit_log", ["time"])

    op.create_table(
        "ingest_log",
        sa.Column("request_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("endpoint", sa.String(100), nullable=False),
        sa.Column("source", sa.String(100)),
        sa.Column("greenhouse_id", UUID(as_uuid=True), sa.ForeignKey("greenhouse.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(20), nullable=False, server_default="received"),
        sa.Column("details", JSONB, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    for view in ["env_measurement_15m", "env_measurement_1m", "plant_signal_1hz_15m", "plant_signal_1hz_1m"]:
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view} CASCADE")

    tables = [
        "ingest_log", "audit_log", "export_job", "object_meta",
        "annotation_review", "annotation", "label_schema",
        "lab_results", "ground_truth_daily", "event_log",
        "env_measurement", "plant_signal_1hz",
        "sensor", "device", "plant", "zone",
        "refresh_token", "users", "greenhouse",
    ]
    for t in tables:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")

    for enum in ["review_decision", "annotation_status", "user_role"]:
        op.execute(f"DROP TYPE IF EXISTS {enum}")
