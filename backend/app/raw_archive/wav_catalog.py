"""Explicit WAV metadata backups, bounded parts and isolated archive restoration.

No account tables, authentication fields, source writes or deletion entrypoints.
Completed exports can be published again without repeating the SQL snapshot.
"""

import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .policy import ArchiveBlocked, Recording, checksum
from .recovery import encode_value, private_directory

FORMAT = "wav_metadata_v1"
PART_BYTES = 16 * 1024**2
ROW_BYTES = 1024**2
FEATURE_FIELDS = """extractor_version calibration_version parameter_hash source_sha256
sample_rate sample_count duration_seconds value_unit mean median rms standard_deviation
minimum maximum quantiles outlier_count outlier_ratio clipping_count clipping_ratio
data_quality_status technical_fault_score technical_fault_reasons biological_candidate_score
biological_candidate_reasons source_quality_counts flatline_count flatline_seconds
spectral_energy_total dominant_frequency_hz spectral_bands coverage_ratio timing_status
missing_duration_seconds sequence_observations sequence_gap_count sequence_missing_count
sequence_reset_count source_dropped_samples_delta is_anomaly anomaly_score anomaly_reasons""".split()
SIGNAL_FIELDS = """mean median rms standard_deviation minimum maximum quantiles
outlier_count outlier_ratio clipping_count clipping_ratio flatline_count flatline_seconds
spectral_energy_total dominant_frequency_hz spectral_bands""".split()

# These are source-independent column allowlists, never model reflection or SELECT *.
COLUMNS = {
    "gateway": {
        "organization": "id",
        "zone": "id organization_id zone_type created_at",
        "gateway": "id zone_id hardware_id created_at",
        "sensor": "id gateway_id mac_address sensor_type claimed_at",
        "wav_file": """id sensor_id gateway_id sensor_mac s3_key content_sha256 sample_rate
duration_seconds coverage_ratio timing_status file_size_bytes started_at ended_at created_at
timestamp_source feature_status feature_verified_at raw_deleted_at pcm_encoding_version
pcm_scale_mv pcm_offset_mv calibration_version""",
        "wav_feature": "id wav_file_id sensor_id gateway_id sensor_mac started_at ended_at "
        + " ".join(FEATURE_FIELDS)
        + " feature_checksum verified_at created_at updated_at",
        "wav_feature_version": """id wav_file_id extractor_version calibration_version
parameter_hash source_sha256 feature_checksum feature_payload verified_at created_at""",
        "visual_reading": """sensor_id kind bucket seconds unit n total total2 minimum maximum
identities""",
        "visual_wav": "wav_id source_sha256 seconds status updated_at",
        "visual_wave": """wav_id sensor_id bucket seconds n total total2 minimum maximum duration""",
        "visual_chunk": """chunk_name range_start range_end change_version archived_version
status archive_manifest source_rows updated_at""",
    },
    "direct": {
        "direct_device": "id organization_id zone_id legacy_sensor_id mode",
        "direct_enrollment": "hardware_id device_id created_at",
        "direct_session": "device_id id config mode",
        "direct_segment": """id device_id session_id bucket first_frame end_frame received_frames
revision published_revision sealed updated_at""",
        "direct_revision": "segment_id revision manifest manifest_sha256 verified_at raw_deleted_at",
        "direct_visual_segment": "segment_id source_revision seconds source updated_at",
        "direct_visual_point": """segment_id device_id bucket channel unit seconds n total total2
minimum maximum duration""",
    },
}
COLUMNS = {
    kind: {name: tuple(fields.split()) for name, fields in tables.items()}
    for kind, tables in COLUMNS.items()
}
SESSION_FIELDS = set(
    """protocol_version device_id session_id session_start_us sample_rate channels
sample_bits channel_labels calibration_version firmware_version""".split()
)
MANIFEST_FIELDS = set(
    """protocol_version device_id session_id bucket_start_us revision config
source_mode analytics_scope first_frame end_frame initial_partial status missing_frame_ranges runs""".split()
)
RUN_FIELDS = {"first_frame", "frame_count", "started_at_us", "key", "sha256", "features"}
SNAPSHOT_FIELDS = {"ETag", "ContentLength", "LastModified", "VersionId"}
RECEIPT_FIELDS = {
    "recording",
    "snapshot",
    "remote_key",
    "destination",
    "verified_at",
    "first_verified_at",
    "source_identity",
}


def raw_json(value):
    return json.dumps(
        value, default=encode_value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


ALLOWLIST_SHA256 = hashlib.sha256(
    raw_json(
        {
            "columns": COLUMNS,
            "session": sorted(SESSION_FIELDS),
            "manifest": sorted(MANIFEST_FIELDS),
            "run": sorted(RUN_FIELDS),
            "snapshot": sorted(SNAPSHOT_FIELDS),
            "receipt": sorted(RECEIPT_FIELDS),
            "feature": FEATURE_FIELDS,
            "signal": SIGNAL_FIELDS,
        }
    )
).hexdigest()


def exact_keys(value, permitted, label, *, complete=False):
    if (
        not isinstance(value, dict)
        or set(value) - set(permitted)
        or (complete and set(value) != set(permitted))
    ):
        raise ArchiveBlocked("Unapproved fields in " + label)


def validate_feature(value, fields):
    exact_keys(value, fields, "scientific feature payload")
    for name in ("technical_fault_reasons", "biological_candidate_reasons", "anomaly_reasons"):
        if name in value and (
            not isinstance(value[name], list)
            or any(
                reason
                not in {
                    "low_coverage",
                    "clipping",
                    "flatline",
                    "sequence_gaps",
                    "dropped_samples",
                    "lead_off",
                    "rail_flags",
                    "jump_flags",
                    "recovery_flags",
                    "robust_outliers",
                }
                for reason in value[name]
            )
        ):
            raise ArchiveBlocked("Unknown scientific classification reason")
    for name in ("quantiles", "spectral_bands", "source_quality_counts"):
        if name in value:
            permitted = {
                "quantiles": {"p01", "p05", "p25", "p75", "p95", "p99"},
                "spectral_bands": {
                    "ultra_low_0_003_0_04_hz",
                    "very_low_0_04_0_15_hz",
                    "low_0_15_0_5_hz",
                    "mid_0_5_1_hz",
                    "high_1_10_hz",
                    "very_high_10_50_hz",
                },
                "source_quality_counts": {
                    "valid",
                    "lead_off",
                    "rail_high",
                    "rail_low",
                    "jump",
                    "recovery",
                    "unknown",
                    "total",
                },
            }[name]
            exact_keys(value[name], permitted, name)
            if any(type(number) not in (int, float) for number in value[name].values()):
                raise ArchiveBlocked("Scientific statistics must be numeric")


def validate_row(kind, table, row):
    fields = COLUMNS.get(kind, {}).get(table)
    if fields is None:
        raise ArchiveBlocked("Unknown archive metadata table")
    exact_keys(row, fields, table, complete=True)
    if table == "wav_feature":
        validate_feature({key: row[key] for key in FEATURE_FIELDS}, FEATURE_FIELDS)
    if table == "wav_feature_version":
        validate_feature(row["feature_payload"], FEATURE_FIELDS)
    if table == "direct_session":
        exact_keys(row["config"], SESSION_FIELDS, "Direct session", complete=True)
        validate_session(row["config"])
    if table == "direct_revision":
        manifest = row["manifest"]
        exact_keys(manifest, MANIFEST_FIELDS, "Direct manifest", complete=True)
        exact_keys(manifest["config"], SESSION_FIELDS, "Direct config", complete=True)
        validate_session(manifest["config"])
        for run in manifest["runs"]:
            exact_keys(run, RUN_FIELDS, "Direct WAV run", complete=True)
            for feature in run["features"]:
                validate_feature(
                    feature,
                    set(SIGNAL_FIELDS)
                    | {"channel", "unit", "sample_count", "extractor_version", "parameter_hash"},
                )
        if hashlib.sha256(raw_json(manifest)).hexdigest() != row["manifest_sha256"]:
            raise ArchiveBlocked("Direct manifest checksum differs from the source")
    encoded = raw_json({"table": table, "row": row})
    if len(encoded) > ROW_BYTES:
        raise ArchiveBlocked("Metadata row exceeds its memory budget")
    return encoded


def validate_session(config):
    from app.direct.protocol import ChunkMetadata

    # Use the existing protocol's explicit bounds and scientific label grammar.
    ChunkMetadata.model_validate(
        config | {"sequence": 0, "first_frame": 0, "frame_count": 1, "payload_sha256": "0" * 64}
    )


def safe_receipt(row):
    exact_keys(row, {"id", "state", "receipt"}, "archive journal", complete=True)
    if row["state"] not in {"copying", "verified", "deleting", "evicted"}:
        raise ArchiveBlocked("Unknown journal state")
    receipt = json.loads(row["receipt"]) if isinstance(row["receipt"], str) else row["receipt"]
    exact_keys(receipt, RECEIPT_FIELDS, "archive receipt")
    record = Recording.from_receipt(receipt["recording"])
    if row["id"] != record.archive_id:
        raise ArchiveBlocked("Journal identity differs from its recording")
    exact_keys(receipt["snapshot"], SNAPSHOT_FIELDS, "source version snapshot")
    normalized = dict(receipt)
    normalized["recording"] = json.loads(raw_json(asdict(record)))
    return {"id": row["id"], "state": row["state"], "receipt": normalized}


class Parts:
    def __init__(self, output, kind, checkpoint, *, compresslevel=6):
        if not 1 <= compresslevel <= 9:
            raise ArchiveBlocked("Invalid metadata compression level")
        self.output, self.kind, self.checkpoint = output, kind, checkpoint
        self.compresslevel = compresslevel
        self.files, self.raw, self.body = [], None, None
        self.rows = self.written = 0

    def write(self, line):
        if self.raw is None or self.written + len(line) + 1 > PART_BYTES:
            self.finish()
            self.path = self.output / f"{self.kind}-{len(self.files):06d}.jsonl.gz"
            self.raw = self.path.open("xb")
            os.chmod(self.path, 0o600)
            self.body = gzip.GzipFile(
                fileobj=self.raw, mode="wb", mtime=0, compresslevel=self.compresslevel
            )
            self.rows = self.written = 0
        self.body.write(line + b"\n")
        self.rows += 1
        self.written += len(line) + 1
        if self.rows % 100 == 0:
            self.checkpoint()

    def finish(self):
        if self.raw is not None:
            self.body.close()
            self.raw.flush()
            os.fsync(self.raw.fileno())
            self.raw.close()
            self.files.append(
                {
                    "kind": self.kind,
                    "file": self.path.name,
                    "sha256": checksum(self.path),
                    "bytes": self.path.stat().st_size,
                    "rows": self.rows,
                    "decoded_bytes": self.written,
                }
            )
            self.raw = self.body = None


def export_metadata(engine, kind, output, checkpoint, *, fetch_rows=10, compresslevel=6):
    from sqlalchemy import text

    if not 1 <= fetch_rows <= 1000:
        raise ArchiveBlocked("Invalid metadata cursor batch")
    parts = Parts(output, kind, checkpoint, compresslevel=compresslevel)
    tables = {}
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as db:
            with db.begin():
                if db.execute(text("SHOW transaction_read_only")).scalar() != "on":
                    raise ArchiveBlocked("Archive source must be read-only")
                for table, fields in COLUMNS[kind].items():
                    checkpoint()
                    count = 0
                    statement = "SELECT " + ",".join('"' + name + '"' for name in fields)
                    result = (
                        db.execution_options(stream_results=True, yield_per=fetch_rows)
                        .execute(text(statement + ' FROM "' + table + '"'))
                        .mappings()
                    )
                    for row in result:
                        parts.write(validate_row(kind, table, dict(row)))
                        count += 1
                    tables[table] = count
    finally:
        parts.finish()
    return parts.files, tables


def export_ledger(path, output, checkpoint):
    if path.is_symlink() or not path.is_absolute() or not path.is_file():
        raise ArchiveBlocked("Existing absolute journal required")
    parts = Parts(output, "ledger", checkpoint)
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA cache_size=-1024")
        db.execute("BEGIN")
        for ident, state, receipt in db.execute("SELECT id,state,receipt FROM archive ORDER BY id"):
            line = raw_json(safe_receipt({"id": ident, "state": state, "receipt": receipt}))
            if len(line) > ROW_BYTES:
                raise ArchiveBlocked("Journal row exceeds its memory budget")
            parts.write(line)
    finally:
        db.close()
        parts.finish()
    return parts.files


def manifest_check(manifest):
    if (
        manifest.get("schema") != 2
        or manifest.get("format") != FORMAT
        or manifest.get("allowlist_sha256") != ALLOWLIST_SHA256
        or set(manifest.get("tables", {})) != {"gateway", "direct"}
    ):
        raise ArchiveBlocked("Explicit WAV-only metadata manifest required")
    for kind in COLUMNS:
        if set(manifest["tables"][kind]) != set(COLUMNS[kind]):
            raise ArchiveBlocked("Metadata manifest omits required tables")
    files = manifest.get("files", [])
    if not files or {entry.get("kind") for entry in files} != {"ledger", "gateway", "direct"}:
        raise ArchiveBlocked("Complete journal and both metadata sources required")
    seen = set()
    for entry in files:
        if entry["file"] in seen or Path(entry["file"]).name != entry["file"]:
            raise ArchiveBlocked("Unsafe or duplicate metadata part")
        seen.add(entry["file"])
        if (
            type(entry.get("rows")) is not int
            or entry["rows"] <= 0
            or not 0 < entry["bytes"] <= PART_BYTES + ROW_BYTES
            or not 0 < entry["decoded_bytes"] <= PART_BYTES
        ):
            raise ArchiveBlocked("Invalid metadata part bounds")


def restore_parts(manifest, files, destination, checkpoint=lambda: None):
    """New SQLite archive index, never users or application tables in a live DB."""
    manifest_check(manifest)
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise ArchiveBlocked("Restore needs a new absolute isolated database")
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    db = sqlite3.connect(destination)
    counts = {kind: {table: 0 for table in tables} for kind, tables in COLUMNS.items()}
    restored = []
    try:
        db.execute("PRAGMA cache_size=-2048")
        db.execute(
            "CREATE TABLE records(kind TEXT,table_name TEXT,ordinal INTEGER,row TEXT,"
            "PRIMARY KEY(kind,table_name,ordinal))"
        )
        db.execute("CREATE TABLE archive(id TEXT PRIMARY KEY,state TEXT,receipt TEXT)")
        for entry in manifest["files"]:
            checkpoint()
            source = files[entry["file"]]
            if (
                source.is_symlink()
                or source.stat().st_size != entry["bytes"]
                or checksum(source) != entry["sha256"]
            ):
                raise ArchiveBlocked("Metadata part readback mismatch")
            size = rows = 0
            with gzip.open(source, "rb") as body:
                while line := body.readline(ROW_BYTES + 2):
                    size += len(line)
                    if len(line) > ROW_BYTES + 1 or size > entry["decoded_bytes"]:
                        raise ArchiveBlocked("Metadata decoding exceeds its budget")
                    value = json.loads(line)
                    kind = entry["kind"]
                    if kind == "ledger":
                        saved = safe_receipt(value)
                        db.execute(
                            "INSERT INTO archive VALUES (?,?,?)",
                            (saved["id"], saved["state"], raw_json(saved["receipt"]).decode()),
                        )
                    else:
                        exact_keys(value, {"table", "row"}, "metadata row", complete=True)
                        table = value["table"]
                        validate_row(kind, table, value["row"])
                        counts[kind][table] += 1
                        db.execute(
                            "INSERT INTO records VALUES (?,?,?,?)",
                            (kind, table, counts[kind][table], raw_json(value["row"]).decode()),
                        )
                    rows += 1
                    if rows % 100 == 0:
                        checkpoint()
            if rows != entry["rows"] or size != entry["decoded_bytes"]:
                raise ArchiveBlocked("Restored metadata rows or bytes differ")
            restored.append(
                {"kind": kind, "rows": rows, "sha256": entry["sha256"], "file": entry["file"]}
            )
        if counts != manifest["tables"]:
            raise ArchiveBlocked("Restored table counts differ from the manifest")
        db.commit()
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ArchiveBlocked("Restored archive index is corrupt")
    finally:
        db.close()
    return restored


def restore_bundle(
    destination, reference, output, namespace, *, snapshot=None, checkpoint=lambda: None
):
    private_directory(output)
    path = output / "manifest.json"
    destination.download_catalog(reference["key"], path, reference["size"], snapshot=snapshot)
    if checksum(path) != reference["sha256"] or path.stat().st_size != reference["size"]:
        raise ArchiveBlocked("Metadata manifest readback mismatch")
    manifest = json.loads(path.read_text())
    manifest_check(manifest)
    if (
        manifest.get("environment") != namespace
        or manifest.get("destination") != destination.identity
    ):
        raise ArchiveBlocked("Metadata archive destination differs")
    files = {}
    for position, entry in enumerate(manifest["files"]):
        checkpoint()
        body = output / f"part-{position:06d}.gz"
        destination.download_catalog(entry["remote_key"], body, entry["bytes"], snapshot=snapshot)
        files[entry["file"]] = body
    restored = restore_parts(manifest, files, output / "wav-catalog.sqlite3", checkpoint)
    return {
        "schema": 2,
        "format": FORMAT,
        "allowlist_sha256": ALLOWLIST_SHA256,
        "environment": namespace,
        "destination": destination.identity,
        "created_at": datetime.now(UTC).isoformat(),
        "catalog_manifest": reference,
        "restored": restored,
        "tables": manifest["tables"],
        "deleted_files": 0,
    }


def main():
    from sqlalchemy import create_engine

    from .runner import destination_from_environment, health_probe, metadata_source_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--publish-existing", type=Path)
    parser.add_argument("--restore-reference", type=Path)
    parser.add_argument("--snapshot")
    args = parser.parse_args()
    from .health import SafetyPause

    probe = health_probe()
    start = time.monotonic()

    def checkpoint():
        if time.monotonic() - start > 120:
            raise ArchiveBlocked("Metadata operation reached its two-minute budget")
        if not probe():
            raise SafetyPause(probe.last.get("reason", "receiver_headroom"), probe.last)

    try:
        checkpoint()
        private_directory(args.output)
        namespace = os.environ.get("RAW_ARCHIVE_ENVIRONMENT", "production")
        if namespace not in {"production", "staging"}:
            raise ArchiveBlocked("Explicit archive environment required")
        if args.restore_reference:
            box = destination_from_environment()
            try:
                result = restore_bundle(
                    box,
                    json.loads(args.restore_reference.read_text()),
                    args.output,
                    namespace,
                    snapshot=args.snapshot,
                    checkpoint=checkpoint,
                )
                proof_path = args.output / "restore-proof.json"
                save_json(proof_path, result)
                reference = {
                    "key": f"{namespace}/catalog-backup/{checksum(proof_path)}.json",
                    "sha256": checksum(proof_path),
                    "size": proof_path.stat().st_size,
                }
                if args.publish:
                    checkpoint()
                    box.publish_catalog(proof_path, reference["key"])
            finally:
                box.close()
            print(
                json.dumps(
                    {
                        "status": "restored",
                        "proof": reference,
                        "published": args.publish,
                        "deleted_files": 0,
                    }
                )
            )
            return
        if args.snapshot:
            parser.error("--snapshot requires --restore-reference")
        if args.publish_existing:
            if args.publish_existing.is_symlink():
                raise ArchiveBlocked("Existing metadata manifest cannot be a symlink")
            manifest = json.loads(args.publish_existing.read_text())
            args.output = args.publish_existing.parent
            private_directory(args.output)
            manifest_check(manifest)
        else:
            if args.ledger is None:
                parser.error("--ledger required for a new export")
            manifest = {
                "schema": 2,
                "format": FORMAT,
                "allowlist_sha256": ALLOWLIST_SHA256,
                "environment": namespace,
                "created_at": datetime.now(UTC).isoformat(),
                "files": [],
                "tables": {},
            }
            manifest["direct_bucket"] = metadata_source_settings("direct", namespace)["bucket"]
            manifest["files"].extend(export_ledger(args.ledger, args.output, checkpoint))
            for kind in COLUMNS:
                engine = create_engine(
                    metadata_source_settings(kind, namespace)["database"],
                    pool_size=1,
                    max_overflow=0,
                    connect_args={
                        "connect_timeout": 5,
                        "options": "-c default_transaction_read_only=on -c statement_timeout=8000 "
                        "-c lock_timeout=150 -c jit=off -c work_mem=2048",
                    },
                )
                try:
                    parts, tables = export_metadata(engine, kind, args.output, checkpoint)
                    manifest["files"].extend(parts)
                    manifest["tables"][kind] = tables
                finally:
                    engine.dispose()
            manifest_check(manifest)
            save_json(args.output / "manifest.json", manifest)
        result = {
            "status": "backed_up",
            "format": FORMAT,
            "deleted_files": 0,
            "manifest": "manifest.json",
            "published": False,
        }
        if args.publish or args.publish_existing:
            box = destination_from_environment()
            try:
                result["offsite_manifest"] = publish(
                    args.output, manifest, box, namespace, checkpoint
                )
                result["published"] = True
            finally:
                box.close()
        save_json(
            args.output
            / ("publication-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + ".json"),
            result,
        )
        print(json.dumps(result))
    finally:
        probe.close()


def save_json(path, data):
    with path.open("xb") as body:
        os.chmod(path, 0o600)
        body.write(raw_json(data))
        body.flush()
        os.fsync(body.fileno())


def publish(output, manifest, box, namespace, checkpoint=lambda: None):
    manifest_check(manifest)
    if manifest["environment"] != namespace:
        raise ArchiveBlocked("Refuse metadata publication across environments")
    remote = dict(manifest, destination=box.identity, files=[])
    for entry in manifest["files"]:
        checkpoint()
        path = output / entry["file"]
        if (
            path.is_symlink()
            or checksum(path) != entry["sha256"]
            or path.stat().st_size != entry["bytes"]
        ):
            raise ArchiveBlocked("Local metadata part changed")
        key = f"{namespace}/catalog-backup/{entry['sha256']}.jsonl.gz"
        box.publish_catalog(path, key)
        remote["files"].append(dict(entry, remote_key=key))
    raw = raw_json(remote)
    if len(raw) > ROW_BYTES:
        raise ArchiveBlocked("Metadata manifest exceeds its budget")
    digest = hashlib.sha256(raw).hexdigest()
    path = output / (digest + "-offsite.json")
    if not path.exists():
        save_json(path, remote)
    elif path.is_symlink() or checksum(path) != digest:
        raise ArchiveBlocked("Existing publication manifest differs")
    checkpoint()
    key = f"{namespace}/catalog-backup/{digest}.json"
    box.publish_catalog(path, key)
    return {"key": key, "sha256": digest, "size": len(raw)}


def entrypoint():
    from .health import SafetyPause

    try:
        main()
        return 0
    except SafetyPause as error:
        print(
            json.dumps(
                {"status": "paused", "reason": error.code, "deleted_files": 0, "complete": False}
            )
        )
        return 75
    except Exception as error:
        # Driver exceptions can contain credentials. Keep details in private diagnostics.
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_type": type(error).__name__,
                    "deleted_files": 0,
                    "complete": False,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(entrypoint())
