"""Explicit PostgreSQL datasets, keyset exports and exact fenced row eviction."""

import hashlib
import os
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta

from sqlalchemy import MetaData, Table, and_, delete, select, text

from app.raw_archive.policy import ArchiveBlocked

from .codec import digest, encode, epoch_us
from .policy import Candidate

GATEWAY_JOIN = "JOIN sensor s ON s.id=t.sensor_id JOIN gateway g ON g.id=s.gateway_id JOIN zone z ON z.id=g.zone_id"
WAV_JOIN = "JOIN wav_file w ON w.id=t.wav_file_id JOIN sensor s ON s.id=w.sensor_id JOIN gateway g ON g.id=s.gateway_id JOIN zone z ON z.id=g.zone_id"


@dataclass(frozen=True)
class Dataset:
    source: str
    table: str
    kind: str
    time: str
    joins: str
    organization: str
    sensor: str
    keys: tuple
    epoch: bool = False


DATASETS = {
    "ingest_log": Dataset(
        "gateway",
        "ingest_log",
        "logs",
        "t.created_at",
        "JOIN gateway g ON g.id=t.gateway_id JOIN zone z ON z.id=g.zone_id",
        "z.organization_id",
        "t.gateway_id",
        ("measurement_id",),
    ),
    "gateway_state_report": Dataset(
        "gateway",
        "gateway_state_report",
        "logs",
        "t.reported_at",
        "JOIN gateway g ON g.id=t.gateway_id JOIN zone z ON z.id=g.zone_id",
        "z.organization_id",
        "t.gateway_id",
        ("id",),
    ),
    "sensor_reading": Dataset(
        "gateway",
        "sensor_reading",
        "raw_readings",
        "t.timestamp",
        GATEWAY_JOIN,
        "z.organization_id",
        "t.sensor_id",
        ("timestamp", "sensor_id", "kind"),
    ),
    "visual_reading": Dataset(
        "gateway",
        "visual_reading",
        "dashboard",
        "t.bucket",
        GATEWAY_JOIN,
        "z.organization_id",
        "t.sensor_id",
        ("sensor_id", "kind", "bucket", "seconds"),
    ),
    "visual_wave": Dataset(
        "gateway",
        "visual_wave",
        "dashboard",
        "t.bucket",
        GATEWAY_JOIN,
        "z.organization_id",
        "t.sensor_id",
        ("wav_id", "bucket", "seconds"),
    ),
    "wav_feature": Dataset(
        "gateway",
        "wav_feature",
        "features",
        "t.updated_at",
        WAV_JOIN,
        "z.organization_id",
        "w.sensor_id",
        ("id",),
    ),
    "wav_feature_version": Dataset(
        "gateway",
        "wav_feature_version",
        "features",
        "t.created_at",
        WAV_JOIN,
        "z.organization_id",
        "w.sensor_id",
        ("id",),
    ),
    "direct_visual_point": Dataset(
        "direct",
        "direct_visual_point",
        "dashboard",
        "t.bucket",
        "JOIN direct_device d ON d.id=t.device_id",
        "d.organization_id",
        "t.device_id",
        ("segment_id", "channel", "bucket"),
        True,
    ),
    "direct_revision": Dataset(
        "direct",
        "direct_revision",
        "features",
        "t.verified_at",
        "JOIN direct_segment s ON s.id=t.segment_id JOIN direct_device d ON d.id=s.device_id",
        "d.organization_id",
        "s.device_id",
        ("segment_id", "revision"),
        True,
    ),
    "direct_chunk": Dataset(
        "direct",
        "direct_chunk",
        "direct_payload",
        "t.received_at",
        "JOIN direct_device d ON d.id=t.device_id",
        "d.organization_id",
        "t.device_id",
        ("id",),
        True,
    ),
}

# Unknown tables are reported, never assigned an implicit expiry. Active state
# and security material stay authoritative in PostgreSQL.
RETAINED_TABLES = {
    "alembic_version",
    "organization",
    "user",
    "users",
    "zone",
    "gateway",
    "sensor",
    "plant",
    "zone_access",
    "plant_sensor_assignment",
    "plant_observation_access",
    "wav_file",
    "visual_wav",
    "visual_chunk",
    "visual_worker",
    "direct_device",
    "direct_budget",
    "direct_pairing",
    "direct_enrollment",
    "direct_session",
    "direct_segment",
    "direct_heartbeat",
    "direct_piece",
    "direct_chunk_payload_ref",
    "direct_visual_segment",
    "direct_visual_worker",
    "direct_recording_locator",
    "tiered_reading_identity",
    "tiered_visual_reference",
    "tiered_wave_reference",
}


def row_identity(spec, row):
    return digest({key: row[key] for key in spec.keys})


class Source:
    def __init__(self, engine, source, environment, *, checkpoint, payload_reader=None):
        if source not in {"gateway", "direct"} or environment not in {"staging", "production"}:
            raise ArchiveBlocked("Explicit SQL source and environment required")
        self.engine, self.source, self.environment = engine, source, environment
        self.checkpoint, self.payload_reader = checkpoint, payload_reader
        self.connection = None
        self.expected = None
        self.table_cache = {}

    def inventory(self):
        with self.engine.connect() as db:
            rows = db.execute(
                text("""SELECT schemaname,relname,pg_total_relation_size(relid) AS bytes,n_live_tup
                FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC""")
            ).mappings()
            return [
                dict(
                    row,
                    classification="archive"
                    if row["relname"] in DATASETS
                    else "active_or_referenced"
                    if row["relname"] in RETAINED_TABLES
                    else "unclassified_blocked",
                )
                for row in rows
            ]

    def export(self, dataset, organization, start, end, *, limit=500, after=None):
        spec = DATASETS[dataset]
        if spec.source != self.source or not 1 <= limit <= 500:
            raise ArchiveBlocked("Dataset does not belong to this bounded source")
        byte_budget = int(os.environ.get("TIERED_ARCHIVE_MAX_SQL_PAGE_BYTES", 8 * 1024**2))
        if not 0 < byte_budget <= 8 * 1024**2:
            raise ArchiveBlocked("SQL page budget exceeds worker ceiling")
        stamp = f"to_timestamp({spec.time})" if spec.epoch else spec.time
        order = ",".join("t." + key for key in spec.keys)
        params = {"org": organization, "start": start, "end": end, "lim": limit}
        cursor = ""
        if after:
            params["after_time"] = after["time"]
            for index, key in enumerate(spec.keys):
                params[f"after_{index}"] = after["row"][key]
            cursor = (
                " AND ("
                + stamp
                + ","
                + order
                + ")>(:after_time,"
                + ",".join(f":after_{i}" for i in range(len(spec.keys)))
                + ")"
            )
        # All expressions come exclusively from DATASETS, never CLI input.
        query = text(f"""SELECT t.*,CAST({spec.organization} AS text) AS _archive_org,
            CAST({spec.sensor} AS text) AS _archive_sensor,{stamp} AS _archive_time
            FROM {spec.table} t {spec.joins}
            WHERE CAST({spec.organization} AS text)=:org AND {stamp}>=:start AND {stamp}<:end {cursor}
            ORDER BY {stamp},{order} LIMIT :lim""")
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as db:
            with db.begin():
                db.execute(text("SET TRANSACTION READ ONLY"))
                db.execute(text("SET LOCAL statement_timeout='8s'"))
                db.execute(text("SET LOCAL lock_timeout='150ms'"))
                required = (
                    "sensor_reading_pkey"
                    if spec.table == "sensor_reading"
                    else "tiered_" + spec.table + "_scan"
                )
                index = db.execute(
                    text(
                        "SELECT indisvalid AND indisready FROM pg_index WHERE indexrelid=to_regclass(:name) AND indrelid=to_regclass(:table)"
                    ),
                    {"name": required, "table": spec.table},
                ).scalar()
                if index is not True:
                    raise ArchiveBlocked("Missing valid bounded scan index: " + required)
                rows = []
                decoded_bytes = 0
                self.next_cursor = None
                self.page_full = False
                for record in db.execute(query.execution_options(yield_per=16), params).mappings():
                    self.checkpoint()
                    body = dict(record)
                    org, sensor, at = (
                        body.pop("_archive_org"),
                        body.pop("_archive_sensor"),
                        body.pop("_archive_time"),
                    )
                    context = self.context(db, spec, body)
                    envelope = {
                        "identity": row_identity(spec, body),
                        "organization": org,
                        "sensor": sensor,
                        "time_us": epoch_us(
                            body["started_at"] if spec.table == "wav_feature" else at
                        ),
                        "body": {"row": body, "context": context},
                    }
                    size = len(encode(envelope))
                    if size > byte_budget:
                        raise ArchiveBlocked("Single SQL row exceeds configured page budget")
                    if rows and decoded_bytes + size > byte_budget:
                        self.page_full = True
                        break
                    self.next_cursor = {"time": at, "row": {key: body[key] for key in spec.keys}}
                    rows.append(envelope)
                    decoded_bytes += size
                self.page_full = self.page_full or len(rows) == limit
                # Identity hash is the stable tiebreaker used by the Parquet codec.
                rows.sort(key=lambda row: (row["time_us"], row["identity"]))
                flags = self.readiness(db, spec, rows, end)
        return rows, flags

    def context(self, db, spec, row):
        if spec.table != "direct_chunk":
            return {}
        pieces = [
            dict(r)
            for r in db.execute(
                text("SELECT * FROM direct_piece WHERE chunk_id=:id ORDER BY segment_id"),
                {"id": row["id"]},
            ).mappings()
        ]
        reference = (
            db.execute(
                text("SELECT * FROM direct_chunk_payload_ref WHERE chunk_id=:id"), {"id": row["id"]}
            )
            .mappings()
            .first()
        )
        session = (
            db.execute(
                text("SELECT config FROM direct_session WHERE device_id=:device AND id=:session"),
                {"device": row["device_id"], "session": row["session_id"]},
            )
            .mappings()
            .one()
        )
        context = {
            "pieces": pieces,
            "payload_reference": dict(reference) if reference else None,
            "session_config": session["config"],
        }
        if row["payload"] is not None:
            payload = bytes(row["payload"])
        elif self.payload_reader is not None:
            payload = self.payload_reader(db, row, context)
        else:
            raise ArchiveBlocked("Direct reconstruction reader required before metadata archival")
        if (
            len(payload) != row["payload_bytes"]
            or hashlib.sha256(payload).hexdigest() != row["payload_sha256"]
        ):
            raise ArchiveBlocked("Direct payload reconstruction differs")
        context["original_payload"] = payload
        return context

    def readiness(self, db, spec, rows, end):
        import os

        result = dict(
            complete=True,
            features_verified=True,
            projections_verified=True,
            dependencies_complete=True,
            active=False,
            quarantined=False,
        )
        if not rows:
            return result
        if spec.kind in {"raw_readings", "dashboard"}:
            result["dependencies_complete"] = (
                os.getenv("TIERED_ARCHIVE_SQL_RETENTION_ACCEPTED", "false") == "true"
            )
            if spec.source == "gateway":
                from .gateway_references import ready

                result["dependencies_complete"] &= ready(db)
                if spec.kind == "dashboard":
                    result["dependencies_complete"] &= all(
                        item["body"]["row"]["seconds"] == 600 for item in rows
                    )
        if spec.table == "ingest_log":
            result["complete"] = all(
                item["body"]["row"]["status"] in {"success", "processed", "failed", "error"}
                for item in rows
            )
            result["dependencies_complete"] = not any(
                item["body"]["row"].get("raw_file_reference") for item in rows
            )
        if spec.kind == "raw_readings":
            for envelope in rows:
                row = envelope["body"]["row"]
                covered = db.execute(
                    text("""SELECT 1 FROM visual_chunk WHERE range_start<=:at AND range_end>:at
                    AND status IN ('verified','compacted') AND change_version=archived_version
                    AND coalesce((archive_manifest::jsonb->>'protected_rows')::bigint,0)=0 LIMIT 1"""),
                    {"at": row["timestamp"]},
                ).first()
                if not covered:
                    result["projections_verified"] = False
                pending = db.execute(
                    text("""SELECT 1 FROM wav_file WHERE sensor_id=:sensor AND started_at<=:at AND ended_at>:at
                    AND (feature_status IS DISTINCT FROM 'verified' OR feature_verified_at IS NULL) LIMIT 1"""),
                    {"sensor": row["sensor_id"], "at": row["timestamp"]},
                ).first()
                if pending:
                    result["features_verified"] = False
        elif spec.table in {"wav_feature", "wav_feature_version"}:
            result["dependencies_complete"] = (
                os.getenv("TIERED_ARCHIVE_FEATURE_READS_ACCEPTED", "false") == "true"
            )
            for envelope in rows:
                row = envelope["body"]["row"]
                ready = db.execute(
                    text("SELECT raw_deleted_at IS NOT NULL FROM wav_file WHERE id=:id"),
                    {"id": row["wav_file_id"]},
                ).scalar_one()
                result["dependencies_complete"] &= bool(ready)
        elif spec.source == "direct":
            for envelope in rows:
                row = envelope["body"]["row"]
                segment_ids = (
                    [p["segment_id"] for p in envelope["body"]["context"]["pieces"]]
                    if spec.table == "direct_chunk"
                    else [row["segment_id"]]
                )
                if not segment_ids:
                    result["complete"] = False
                for identity in segment_ids:
                    state = (
                        db.execute(
                            text("""SELECT s.sealed,s.revision,s.published_revision,v.source_revision,v.error,
                                v.source AS projection_source,r.manifest->>'status' AS manifest_status
                        FROM direct_segment s LEFT JOIN direct_visual_segment v ON v.segment_id=s.id
                        LEFT JOIN direct_revision r ON r.segment_id=s.id AND r.revision=s.published_revision WHERE s.id=:id"""),
                            {"id": identity},
                        )
                        .mappings()
                        .one()
                    )
                    result["complete"] &= bool(
                        state["sealed"]
                        and state["revision"] == state["published_revision"]
                        and state["manifest_status"] == "complete"
                    )
                    result["projections_verified"] &= bool(
                        state["source_revision"] == state["revision"]
                        and state["error"] is None
                        and state["projection_source"] == "wav"
                    )
                # Revision manifests remain local while original download routes
                # or chunk reconstruction still reference them.
                if spec.table == "direct_revision":
                    result["dependencies_complete"] = bool(
                        row["raw_deleted_at"] is not None
                        and os.getenv("TIERED_ARCHIVE_DIRECT_LOCATORS_ACCEPTED", "false") == "true"
                    )
                if spec.table == "direct_chunk" and row["payload"] is not None:
                    # Existing verified offload owns spool accounting. It must
                    # first replace inline bytes with accepted WAV references.
                    result["dependencies_complete"] = False
        return result

    def candidate(self, dataset, organization, start, end, metadata, flags, *, settled_at):
        spec = DATASETS[dataset]
        return Candidate(
            self.environment,
            self.source,
            dataset,
            organization,
            start,
            end,
            metadata["content_sha256"],
            metadata["rows"],
            spec.kind,
            settled_at,
            late_until=end + timedelta(days=7) if self.source == "direct" else None,
            **flags,
        )

    def table(self, name):
        if name not in self.table_cache:
            self.table_cache[name] = Table(name, MetaData(), autoload_with=self.connection)
        return self.table_cache[name]

    @contextmanager
    def exclusive(self, candidate):
        if self.connection is not None or not self.expected:
            raise ArchiveBlocked("Exact independently restored source rows required")
        with self.engine.begin() as db:
            self.connection = db
            try:
                db.execute(text("SET LOCAL lock_timeout='150ms'"))
                db.execute(text("SET LOCAL statement_timeout='3s'"))
                # Locks are acquired on exact primary keys, not the live table.
                spec = DATASETS[candidate.dataset]
                if spec.table == "sensor_reading":
                    from .gateway_references import fence

                    fence(db, [item["body"]["row"] for item in self.expected])
                table = self.table(spec.table)
                for envelope in self.expected:
                    key = envelope["body"]["row"]
                    condition = and_(*(table.c[column] == key[column] for column in spec.keys))
                    db.execute(select(table).where(condition).with_for_update(nowait=True)).first()
                    if spec.table == "direct_chunk":
                        for child in ("direct_piece", "direct_chunk_payload_ref"):
                            child_table = self.table(child)
                            db.execute(
                                select(child_table)
                                .where(child_table.c.chunk_id == key["id"])
                                .with_for_update(nowait=True)
                            ).all()
                        for piece in envelope["body"]["context"]["pieces"]:
                            segment = self.table("direct_segment")
                            db.execute(
                                select(segment)
                                .where(segment.c.id == piece["segment_id"])
                                .with_for_update(nowait=True)
                            ).one()
                yield
            finally:
                self.connection = None

    def present(self):
        spec = DATASETS[self.current.dataset]
        table = self.table(spec.table)
        rows = []
        for envelope in self.expected:
            expected = envelope["body"]["row"]
            condition = and_(*(table.c[column] == expected[column] for column in spec.keys))
            actual = self.connection.execute(select(table).where(condition)).mappings().first()
            if actual is not None:
                params = {f"key_{index}": expected[key] for index, key in enumerate(spec.keys)}
                predicate = " AND ".join(
                    f"t.{key}=:key_{index}" for index, key in enumerate(spec.keys)
                )
                scope = self.connection.execute(
                    text(
                        f"SELECT CAST({spec.organization} AS text),CAST({spec.sensor} AS text) FROM {spec.table} t {spec.joins} WHERE {predicate}"
                    ),
                    params,
                ).one_or_none()
                if scope is None or tuple(scope) != (envelope["organization"], envelope["sensor"]):
                    raise ArchiveBlocked("Source ownership or sensor binding changed")
                if encode(dict(actual)) != encode(expected):
                    raise ArchiveBlocked("Source row changed after archival")
                context = self.context(self.connection, spec, dict(actual))
                if encode(context) != encode(envelope["body"]["context"]):
                    raise ArchiveBlocked("Source dependency changed after archival")
                rows.append(envelope)
        if rows and len(rows) != len(self.expected):
            raise ArchiveBlocked("Partially missing source needs explicit reconciliation")
        return rows

    def inspect(self, candidate):
        self.current = candidate
        rows = self.present()
        flags = self.readiness(self.connection, DATASETS[candidate.dataset], rows, candidate.end)
        return replace(candidate, **flags)

    def absent(self, candidate):
        self.current = candidate
        return not self.present()

    def delete(self, candidate):
        spec = DATASETS[candidate.dataset]
        table = self.table(spec.table)
        if spec.source == "gateway":
            from .gateway_references import preserve

            preserve(
                self.connection,
                spec.table,
                [item["body"]["row"] for item in self.expected],
                candidate.identity,
            )
        for envelope in self.expected:
            row = envelope["body"]["row"]
            if spec.table == "direct_revision":
                import json

                manifest = dict(row["manifest"])
                manifest["runs"] = [
                    {key: value for key, value in run.items() if key != "features"}
                    for run in manifest["runs"]
                ]
                self.connection.execute(
                    text("""INSERT INTO direct_recording_locator(segment_id,revision,manifest_sha256,manifest,archive_candidate)
                    VALUES(:segment,:revision,:sha,CAST(:manifest AS jsonb),:candidate)
                    ON CONFLICT(segment_id,revision) DO NOTHING"""),
                    {
                        "segment": row["segment_id"],
                        "revision": row["revision"],
                        "sha": row["manifest_sha256"],
                        "manifest": json.dumps(manifest, sort_keys=True),
                        "candidate": candidate.identity,
                    },
                )
                saved = self.connection.execute(
                    text(
                        "SELECT manifest_sha256,archive_candidate FROM direct_recording_locator WHERE segment_id=:segment AND revision=:revision"
                    ),
                    {"segment": row["segment_id"], "revision": row["revision"]},
                ).one()
                if tuple(saved) != (row["manifest_sha256"], candidate.identity):
                    raise ArchiveBlocked("Direct locator conflict")
            if spec.table == "direct_chunk":
                # Child rows were included in the same lossless archive body.
                for child in ("direct_piece", "direct_chunk_payload_ref"):
                    self.connection.execute(
                        delete(self.table(child)).where(self.table(child).c.chunk_id == row["id"])
                    )
            condition = and_(*(table.c[column] == row[column] for column in spec.keys))
            if self.connection.execute(delete(table).where(condition)).rowcount != 1:
                raise ArchiveBlocked("Exact row deletion did not affect one row")
