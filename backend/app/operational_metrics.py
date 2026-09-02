"""Cached-cost operational metrics for storage and pipeline health."""

import logging
from datetime import UTC, datetime, timedelta

from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import func, text

from app.config import settings
from app.database import SessionLocal
from app.models.master import Gateway
from app.models.operations import BackgroundWorkerHeartbeat
from app.models.wav_file import WavFeature, WavFile

logger = logging.getLogger(__name__)


class OperationalCollector:
    def collect(self):
        db = SessionLocal()
        try:
            now = datetime.now(UTC)
            status_rows = (
                db.query(WavFile.feature_status, func.count(WavFile.id))
                .group_by(WavFile.feature_status)
                .all()
            )
            feature_status = GaugeMetricFamily(
                "greenmind_wav_feature_files", "WAV files by feature state", labels=["status"]
            )
            for status, count in status_rows:
                feature_status.add_metric([status], count)
            yield feature_status

            old_pending = (
                db.query(func.count(WavFile.id))
                .filter(
                    WavFile.feature_status.in_(("pending", "failed", "processing")),
                    WavFile.started_at < now - timedelta(hours=24),
                )
                .scalar()
                or 0
            )
            old_metric = GaugeMetricFamily(
                "greenmind_wav_feature_old_pending", "Unverified WAV files older than 24 hours"
            )
            old_metric.add_metric([], old_pending)
            yield old_metric

            raw_bytes = (
                db.query(func.coalesce(func.sum(WavFile.file_size_bytes), 0))
                .filter(WavFile.raw_deleted_at.is_(None))
                .scalar()
                or 0
            )
            archive_bytes = (
                db.query(
                    func.coalesce(
                        func.sum(
                            func.coalesce(WavFeature.anomaly_file_size_bytes, 0)
                            + func.coalesce(WavFeature.flac_file_size_bytes, 0)
                        ),
                        0,
                    )
                ).scalar()
                or 0
            )
            database_bytes = db.execute(
                text("SELECT pg_database_size(current_database())")
            ).scalar()
            storage = GaugeMetricFamily(
                "greenmind_storage_bytes", "GreenMind storage consumption", labels=["kind"]
            )
            storage.add_metric(["postgresql"], database_bytes)
            storage.add_metric(["raw_wav_metadata"], raw_bytes)
            storage.add_metric(["derived_archives"], archive_bytes)
            yield storage

            ratio = GaugeMetricFamily(
                "greenmind_storage_budget_ratio", "Estimated configured storage budget ratio"
            )
            ratio.add_metric(
                [],
                min(
                    1.0,
                    (database_bytes + raw_bytes + archive_bytes) / settings.storage_capacity_bytes,
                ),
            )
            yield ratio

            table_sizes = GaugeMetricFamily(
                "greenmind_postgresql_table_bytes",
                "PostgreSQL table and index size",
                labels=["table"],
            )
            for table_name in ("sensor_reading", "ingest_log", "wav_file", "wav_feature"):
                if table_name == "sensor_reading":
                    size = db.execute(text("SELECT hypertable_size('sensor_reading')")).scalar()
                else:
                    size = db.execute(
                        text("SELECT pg_total_relation_size(CAST(:table_name AS regclass))"),
                        {"table_name": table_name},
                    ).scalar()
                table_sizes.add_metric([table_name], size)
            yield table_sizes

            offline_count = (
                db.query(func.count(Gateway.id))
                .filter(
                    Gateway.is_active.is_(True),
                    (Gateway.last_seen.is_(None))
                    | (
                        Gateway.last_seen
                        < now - timedelta(minutes=settings.gateway_offline_minutes)
                    ),
                )
                .scalar()
                or 0
            )
            gateways = GaugeMetricFamily(
                "greenmind_gateways_offline", "Active gateways beyond the offline threshold"
            )
            gateways.add_metric([], offline_count)
            yield gateways

            backlog_files, backlog_bytes = db.query(
                func.coalesce(func.sum(Gateway.wav_pending_files), 0),
                func.coalesce(func.sum(Gateway.wav_pending_bytes), 0),
            ).one()
            backlog = GaugeMetricFamily(
                "greenmind_gateway_wav_backlog", "Reported gateway WAV backlog", labels=["unit"]
            )
            backlog.add_metric(["files"], backlog_files)
            backlog.add_metric(["bytes"], backlog_bytes)
            yield backlog

            retention = GaugeMetricFamily(
                "greenmind_retention_state", "Retention configuration state", labels=["mode"]
            )
            retention.add_metric(["enabled"], int(settings.retention_enabled))
            retention.add_metric(["dry_run"], int(settings.retention_dry_run))
            yield retention

            heartbeats = GaugeMetricFamily(
                "greenmind_worker_heartbeat_age_seconds",
                "Background worker heartbeat age",
                labels=["worker", "status"],
            )
            for heartbeat in db.query(BackgroundWorkerHeartbeat).all():
                timestamp = heartbeat.heartbeat_at
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=UTC)
                heartbeats.add_metric(
                    [heartbeat.worker_name, heartbeat.status],
                    max(0.0, (now - timestamp).total_seconds()),
                )
            yield heartbeats
        except Exception:
            logger.exception("Operational metric collection failed")
        finally:
            db.close()
