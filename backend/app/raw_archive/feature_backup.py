"""Append-only scientific feature backup, independent of deletion readiness.

Source PostgreSQL remains authoritative. No RAW, accounts, secrets or deletes.
"""

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from . import wav_catalog as catalog
from .health import SafetyPause
from .policy import ArchiveBlocked, checksum
from .remote_lease import catalog_lease

FORMAT = "wav_features_v1"
TABLES = {
    "gateway": ("wav_feature", "wav_feature_version"),
    "direct": ("direct_segment", "direct_revision"),
}
MAX_BYTES = 256 * 1024**2
FORMAT_V2 = "wav_features_v2"
FORMAT_DIRECT_REVISION = "direct_feature_revision_v1"
TABLES_DIRECT_REVISION = {"direct": ("direct_feature_result",)}
TABLES_V2 = {
    "gateway": TABLES["gateway"],
    "direct": TABLES["direct"] + ("direct_feature_result", "direct_feature_job"),
}
FEATURE_V2_COLUMNS = {
    "direct_feature_result": (
        "id",
        "segment_id",
        "revision",
        "manifest_sha256",
        "input_sha256",
        "inputs",
        "payload",
        "payload_sha256",
        "created_at",
        "verified_at",
    ),
    "direct_feature_job": (
        "id",
        "target_sha256",
        "target",
        "status",
        "claim_token",
        "claim_until",
        "retry_at",
        "attempts",
        "error",
        "created_at",
    ),
}
FEATURE_V2_NESTED = {
    "inputs": "device_id session_id segment_id revision manifest_sha256 wav_key wav_sha256 first_frame frame_count channel_index channel_label extractor_version parameter_sha256 calibration_sha256 quality_context_sha256 calculator_source_sha256".split(),
    "payload": "contract_version calculator_source_sha256 sample_rate_hz sample_count calibration calibration_sha256 raw_clip_bounds features anomaly_index units continuity source_quality_counts coverage_ratio quality".split(),
    "calibration": "status scale offset unit source channel encoding validity".split(),
    "quality": "data_quality_status technical_fault_reasons biological_candidate biological_candidate_reasons observation_status".split(),
    "observations": "coverage source_flags dropped_samples continuity".split(),
    "units": "amplitude spectral_energy frequency time ratio".split(),
    "continuity": "frame_gap_count sequence_gap_count source_dropped_samples_delta".split(),
    "source_quality_counts": "valid lead_off rail_high rail_low jump recovery".split(),
    "job_target": "segment_id revision manifest_sha256 implementation".split(),
    "implementation": "contract_version contract_source_sha256 calculator_source_sha256 quality_source_sha256".split(),
    "signal": catalog.SIGNAL_FIELDS,
}
FEATURE_V2_ALLOWLIST_SHA256 = hashlib.sha256(
    catalog.raw_json(
        {
            "base": catalog.ALLOWLIST_SHA256,
            "tables": TABLES_V2,
            "columns": FEATURE_V2_COLUMNS,
            "nested": FEATURE_V2_NESTED,
            "format": FORMAT_V2,
        }
    )
).hexdigest()
PRE_DIRECT_V2_FEATURE_ALLOWLIST_SHA256 = hashlib.sha256(
    catalog.raw_json(
        {
            "base": catalog.CAPTURE_ALLOWLIST_SHA256,
            "tables": TABLES_V2,
            "columns": FEATURE_V2_COLUMNS,
            "nested": FEATURE_V2_NESTED,
            "format": FORMAT_V2,
        }
    )
).hexdigest()


DIRECT_REVISION_ALLOWLIST_SHA256 = hashlib.sha256(
    catalog.raw_json(
        {
            "base": FEATURE_V2_ALLOWLIST_SHA256,
            "format": FORMAT_DIRECT_REVISION,
            "tables": TABLES_DIRECT_REVISION,
            "scope": ["segment_id", "revision", "manifest_sha256"],
        }
    )
).hexdigest()
PRE_DIRECT_V2_REVISION_ALLOWLIST_SHA256 = hashlib.sha256(
    catalog.raw_json(
        {
            "base": PRE_DIRECT_V2_FEATURE_ALLOWLIST_SHA256,
            "format": FORMAT_DIRECT_REVISION,
            "tables": TABLES_DIRECT_REVISION,
            "scope": ["segment_id", "revision", "manifest_sha256"],
        }
    )
).hexdigest()


def _tables_for(manifest):
    if (
        manifest.get("format") == FORMAT
        and manifest.get("schema") == 1
        and manifest.get("allowlist_sha256")
        in {
            catalog.ALLOWLIST_SHA256,
            catalog.CAPTURE_ALLOWLIST_SHA256,
            catalog.LEGACY_ALLOWLIST_SHA256,
        }
    ):
        return TABLES
    if (
        manifest.get("format") == FORMAT_V2
        and manifest.get("schema") == 2
        and manifest.get("allowlist_sha256")
        in {FEATURE_V2_ALLOWLIST_SHA256, PRE_DIRECT_V2_FEATURE_ALLOWLIST_SHA256}
    ):
        return TABLES_V2
    if (
        manifest.get("format") == FORMAT_DIRECT_REVISION
        and manifest.get("schema") == 3
        and manifest.get("allowlist_sha256")
        in {DIRECT_REVISION_ALLOWLIST_SHA256, PRE_DIRECT_V2_REVISION_ALLOWLIST_SHA256}
    ):
        return TABLES_DIRECT_REVISION
    raise ArchiveBlocked("Unknown feature backup version or allowlist")


def validate_v2_row(kind, table, row):
    if (
        kind == "gateway"
        and table == "wav_feature_version"
        and isinstance(row.get("feature_payload"), dict)
        and row["feature_payload"].get("schema") == "gateway-signal-v1"
    ):
        from app.services.signal_feature_contract import canonical_sha256
        from app.services.signal_quality_v1 import classify_quality_v1

        catalog.exact_keys(row, catalog.COLUMNS[kind][table], table, complete=True)
        payload = row["feature_payload"]
        catalog.exact_keys(
            payload,
            {"schema", "wav_file_id", "source_sha256", "signal"},
            "gateway signal",
            complete=True,
        )
        signal = payload["signal"]
        catalog.exact_keys(
            signal, FEATURE_V2_NESTED["payload"], "gateway signal payload", complete=True
        )
        for key in ("calibration", "units", "quality", "source_quality_counts"):
            catalog.exact_keys(signal[key], FEATURE_V2_NESTED[key], "gateway " + key, complete=True)
        catalog.exact_keys(
            signal["continuity"], FEATURE_V2_NESTED["continuity"], "gateway continuity"
        )
        catalog.exact_keys(
            signal["features"], FEATURE_V2_NESTED["signal"], "gateway feature values", complete=True
        )
        catalog.exact_keys(
            signal["features"]["quantiles"],
            {"p01", "p05", "p25", "p75", "p95", "p99"},
            "gateway quantiles",
            complete=True,
        )
        catalog.exact_keys(
            signal["quality"]["observation_status"],
            FEATURE_V2_NESTED["observations"],
            "gateway observations",
            complete=True,
        )
        allowed_bands = {
            "ultra_low_0_003_0_04_hz",
            "very_low_0_04_0_15_hz",
            "low_0_15_0_5_hz",
            "mid_0_5_1_hz",
            "high_1_10_hz",
            "very_high_10_50_hz",
            "noise_50_nyquist_hz",
        }
        catalog.exact_keys(
            signal["features"]["spectral_bands"], allowed_bands, "gateway spectral bands"
        )
        quality = signal["quality"]
        try:
            if (
                not re.fullmatch(r"gateway-signal-v1-[0-9a-f]{16}", row["extractor_version"])
                or str(row["wav_file_id"]) != payload["wav_file_id"]
                or row["source_sha256"] != payload["source_sha256"]
                or row["feature_checksum"] != canonical_sha256(payload)
                or signal["calibration_sha256"] != canonical_sha256(signal["calibration"])
                or signal["contract_version"] != "signal-contract-v1"
                or signal["calibration"]["unit"] != "mV"
                or signal["units"]
                != {
                    "amplitude": "mV",
                    "spectral_energy": "mV^2",
                    "frequency": "Hz",
                    "time": "s",
                    "ratio": "dimensionless",
                }
                or type(signal["sample_count"]) is not int
                or signal["sample_count"] <= 0
                or type(signal["sample_rate_hz"]) is not int
                or signal["sample_rate_hz"] <= 0
                or type(signal["anomaly_index"]) is not int
                or not 0 <= signal["anomaly_index"] < signal["sample_count"]
                or quality
                != classify_quality_v1(
                    signal["features"],
                    signal["continuity"],
                    signal["source_quality_counts"],
                    signal["coverage_ratio"],
                    quality["observation_status"],
                )
            ):
                raise ValueError("Gateway signal identity mismatch")
        except (ValueError, KeyError, TypeError) as exc:
            raise ArchiveBlocked("Invalid Gateway signal record") from exc
        encoded = catalog.raw_json({"table": table, "row": row})
        if len(encoded) > catalog.ROW_BYTES:
            raise ArchiveBlocked("Feature row exceeds memory budget")
        return encoded
    if table not in FEATURE_V2_COLUMNS:
        return catalog.validate_row(kind, table, row)
    if kind != "direct":
        raise ArchiveBlocked("Invalid direct feature source")
    catalog.exact_keys(row, FEATURE_V2_COLUMNS[table], table, complete=True)
    from app.services.signal_feature_contract import canonical_sha256

    try:
        if table == "direct_feature_result":
            from app.direct.feature_results import validate_result

            validate_result(row["inputs"], row["payload"], require_current=False)
            if (
                row["input_sha256"] != canonical_sha256(row["inputs"])
                or row["payload_sha256"] != canonical_sha256(row["payload"])
                or (row["segment_id"], row["revision"], row["manifest_sha256"])
                != (
                    row["inputs"]["segment_id"],
                    row["inputs"]["revision"],
                    row["inputs"]["manifest_sha256"],
                )
            ):
                raise ValueError("Result identity mismatch")
        else:
            target = row["target"]
            implementation = target.get("implementation", {})
            if (
                set(target) != {"segment_id", "revision", "manifest_sha256", "implementation"}
                or set(implementation)
                != {
                    "contract_version",
                    "contract_source_sha256",
                    "calculator_source_sha256",
                    "quality_source_sha256",
                }
                or row["target_sha256"] != canonical_sha256(target)
                or row["status"] not in {"pending", "claimed", "done", "failed"}
                or type(row["attempts"]) is not int
                or row["attempts"] < 0
                or (row["status"] == "claimed")
                != (row["claim_token"] is not None and row["claim_until"] is not None)
            ):
                raise ValueError("Invalid job")
            for digest in (
                target["manifest_sha256"],
                implementation["contract_source_sha256"],
                implementation["calculator_source_sha256"],
                implementation["quality_source_sha256"],
            ):
                if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
                    raise ValueError("Invalid job digest")
    except (ValueError, KeyError, TypeError) as exc:
        raise ArchiveBlocked("Invalid Direct feature record") from exc
    encoded = catalog.raw_json({"table": table, "row": row})
    if len(encoded) > catalog.ROW_BYTES:
        raise ArchiveBlocked("Feature row exceeds memory budget")
    return encoded


def check_manifest(manifest):
    catalog.exact_keys(
        manifest,
        {
            "schema",
            "format",
            "environment",
            "allowlist_sha256",
            "created_at",
            "tables",
            "files",
            "destination",
            "scientific_coverage",
        },
        "feature manifest",
    )
    tables = _tables_for(manifest)
    if (
        manifest.get("environment") != "production"
        or not isinstance(manifest.get("tables"), dict)
        or set(manifest["tables"]) != set(tables)
    ):
        raise ArchiveBlocked("Explicit feature-only manifest required")
    if manifest["format"] == FORMAT_DIRECT_REVISION:
        coverage = manifest.get("scientific_coverage")
        if (
            not isinstance(coverage, dict)
            or set(coverage) != {"segment_id", "revision", "manifest_sha256"}
            or not isinstance(coverage["segment_id"], str)
            or not coverage["segment_id"]
            or type(coverage["revision"]) is not int
            or coverage["revision"] < 1
            or not isinstance(coverage["manifest_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", coverage["manifest_sha256"])
        ):
            raise ArchiveBlocked("Exact Direct revision coverage required")
    elif "scientific_coverage" in manifest:
        raise ArchiveBlocked("Unexpected scoped feature coverage")
    for kind, names in tables.items():
        counts = manifest["tables"][kind]
        if (
            not isinstance(counts, dict)
            or set(counts) != set(names)
            or any(type(n) is not int or n < 0 for n in counts.values())
        ):
            raise ArchiveBlocked("Incomplete feature table counts")
    if (
        manifest["format"] == FORMAT_DIRECT_REVISION
        and manifest["tables"]["direct"]["direct_feature_result"] < 1
    ):
        raise ArchiveBlocked("Direct revision feature history is empty")
    entries = manifest.get("files")
    if not isinstance(entries, list) or len(entries) > 256:
        raise ArchiveBlocked("Invalid feature parts")
    seen = set()
    for entry in entries:
        catalog.exact_keys(
            entry,
            {
                "kind",
                "file",
                "sha256",
                "bytes",
                "decoded_bytes",
                "rows",
                "remote_key",
                "cipher_sha256",
                "cipher_bytes",
            },
            "feature part",
        )
        if (
            entry.get("kind") not in TABLES
            or not isinstance(entry.get("file"), str)
            or not re.fullmatch(r"(?:gateway|direct)-[0-9]{6}\.jsonl\.gz", entry["file"])
            or entry["file"] in seen
            or not isinstance(entry.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
            or type(entry.get("bytes")) is not int
            or not 0 < entry["bytes"] <= catalog.PART_BYTES + catalog.ROW_BYTES
            or type(entry.get("decoded_bytes")) is not int
            or not 0 < entry["decoded_bytes"] <= catalog.PART_BYTES
            or type(entry.get("rows")) is not int
            or entry["rows"] <= 0
        ):
            raise ArchiveBlocked("Unsafe feature part")
        if manifest["format"] == FORMAT_V2 and "destination" in manifest:
            if (
                not isinstance(entry.get("cipher_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["cipher_sha256"])
                or type(entry.get("cipher_bytes")) is not int
                or not 0 < entry["cipher_bytes"] <= 64 * 1024**2
                or entry.get("remote_key")
                != f"production/catalog-backup/{entry['cipher_sha256']}.bin"
            ):
                raise ArchiveBlocked("Global feature parts must be authenticated and encrypted")
        elif any(key in entry for key in ("cipher_sha256", "cipher_bytes")):
            raise ArchiveBlocked("Unexpected encrypted feature fields")
        seen.add(entry["file"])
    if sum(entry["bytes"] for entry in entries) > MAX_BYTES:
        raise ArchiveBlocked("Feature backup exceeds bounded spool")
    if sum(entry["decoded_bytes"] for entry in entries) > 512 * 1024**2:
        raise ArchiveBlocked("Feature decoding exceeds bounded spool")
    for kind in tables:
        if sum(e["rows"] for e in entries if e["kind"] == kind) != sum(
            manifest["tables"][kind].values()
        ):
            raise ArchiveBlocked("Feature part counts differ")


def restore(manifest, files, destination, checkpoint=lambda: None):
    """Every compressed row is decoded into a separate empty archive database."""
    check_manifest(manifest)
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise ArchiveBlocked("Feature restore requires a new isolated database")
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    tables = _tables_for(manifest)
    counts = {kind: dict.fromkeys(names, 0) for kind, names in tables.items()}
    with closing(sqlite3.connect(destination)) as db, db:
        db.execute("PRAGMA cache_size=-2048")
        db.execute(
            "CREATE TABLE records(kind TEXT, table_name TEXT, ordinal INTEGER, body TEXT, PRIMARY KEY(kind,table_name,ordinal))"
        )
        if manifest["format"] in {FORMAT_V2, FORMAT_DIRECT_REVISION}:
            db.execute(
                "CREATE TABLE restored_direct_feature_result (id TEXT PRIMARY KEY, segment_id TEXT NOT NULL, revision INTEGER NOT NULL, manifest_sha256 TEXT NOT NULL, input_sha256 TEXT NOT NULL UNIQUE, inputs TEXT NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL, created_at REAL NOT NULL, verified_at REAL)"
            )
            db.execute(
                "CREATE TABLE restored_direct_feature_job (id TEXT PRIMARY KEY, target_sha256 TEXT NOT NULL UNIQUE, target TEXT NOT NULL, status TEXT NOT NULL, claim_token TEXT, claim_until REAL, retry_at REAL NOT NULL, attempts INTEGER NOT NULL, error TEXT, created_at REAL NOT NULL)"
            )
        for entry in manifest["files"]:
            checkpoint()
            path = files[entry["file"]]
            if (
                path.is_symlink()
                or path.stat().st_size != entry["bytes"]
                or checksum(path) != entry["sha256"]
            ):
                raise ArchiveBlocked("Feature readback checksum mismatch")
            rows = decoded = 0
            with gzip.open(path, "rb") as body:
                while line := body.readline(catalog.ROW_BYTES + 2):
                    decoded += len(line)
                    if len(line) > catalog.ROW_BYTES + 1 or decoded > entry["decoded_bytes"]:
                        raise ArchiveBlocked("Feature decoding exceeds budget")
                    value = json.loads(line)
                    catalog.exact_keys(value, {"table", "row"}, "feature row", complete=True)
                    kind, table = entry["kind"], value["table"]
                    if table not in tables[kind]:
                        raise ArchiveBlocked("Unapproved feature table")
                    if (
                        table == "direct_revision"
                        and value["row"].get("manifest", {}).get("protocol_version") == 2
                        and manifest["allowlist_sha256"]
                        not in {
                            catalog.ALLOWLIST_SHA256,
                            FEATURE_V2_ALLOWLIST_SHA256,
                            DIRECT_REVISION_ALLOWLIST_SHA256,
                        }
                    ):
                        raise ArchiveBlocked(
                            "Direct protocol v2 requires the versioned metadata allowlist"
                        )
                    if manifest["format"] in {FORMAT_V2, FORMAT_DIRECT_REVISION}:
                        validate_v2_row(kind, table, value["row"])
                    else:
                        if (
                            manifest["allowlist_sha256"] == catalog.LEGACY_ALLOWLIST_SHA256
                            and table == "direct_revision"
                            and "capture_assignment" in value["row"].get("manifest", {})
                        ):
                            raise ArchiveBlocked(
                                "Server capture requires the versioned metadata allowlist"
                            )
                        catalog.validate_row(kind, table, value["row"])
                    if (
                        manifest["format"] == FORMAT_DIRECT_REVISION
                        and {
                            key: value["row"][key]
                            for key in ("segment_id", "revision", "manifest_sha256")
                        }
                        != manifest["scientific_coverage"]
                    ):
                        raise ArchiveBlocked("Feature row is outside scoped revision")
                    counts[kind][table] += 1
                    if (
                        manifest["format"] in {FORMAT_V2, FORMAT_DIRECT_REVISION}
                        and table in FEATURE_V2_COLUMNS
                    ):
                        columns = FEATURE_V2_COLUMNS[table]
                        values = [
                            catalog.raw_json(value["row"][name]).decode()
                            if name in {"inputs", "payload", "target"}
                            else value["row"][name]
                            for name in columns
                        ]
                        db.execute(
                            "INSERT INTO restored_"
                            + table
                            + " ("
                            + ",".join(columns)
                            + ") VALUES ("
                            + ",".join("?" for _ in columns)
                            + ")",
                            values,
                        )
                    db.execute(
                        "INSERT INTO records VALUES (?,?,?,?)",
                        (kind, table, counts[kind][table], catalog.raw_json(value["row"]).decode()),
                    )
                    rows += 1
                    if rows % 100 == 0:
                        checkpoint()
            if rows != entry["rows"] or decoded != entry["decoded_bytes"]:
                raise ArchiveBlocked("Feature readback length mismatch")
        if counts != manifest["tables"]:
            raise ArchiveBlocked("Feature restoration counts differ")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ArchiveBlocked("Feature restoration integrity failure")
    return counts


def publish_and_restore(
    output, manifest, box, checkpoint=lambda: None, *, verification_box=None, encryption_key=None
):
    check_manifest(manifest)
    if manifest["format"] == FORMAT_V2 and (
        not isinstance(encryption_key, bytes) or len(encryption_key) != 32
    ):
        raise ArchiveBlocked("A 32-byte encryption key is required for global feature backups")
    checkpoint()
    capacity = box.capacity()
    size = sum(entry["bytes"] for entry in manifest["files"])
    # Leave both 5% and 2 GiB, plus twice this upload for concurrent allocation.
    required = max(2 * 1024**3, (capacity["total_bytes"] + 19) // 20) + size * 2
    if capacity["available_bytes"] < required:
        raise SafetyPause("storage_box_space", {"required_bytes": required, **capacity})
    remote = dict(manifest, destination=box.identity, files=[])
    for entry in manifest["files"]:
        checkpoint()
        path = output / entry["file"]
        if (
            path.is_symlink()
            or checksum(path) != entry["sha256"]
            or path.stat().st_size != entry["bytes"]
        ):
            raise ArchiveBlocked("Local features changed before publication")
        if manifest["format"] == FORMAT_V2:
            from app.tiered_archive.backup import decrypt, encrypt

            sealed = output / (entry["file"] + ".bin")
            if sealed.exists():
                if sealed.is_symlink():
                    raise ArchiveBlocked("Encrypted feature part must not be a symlink")
                with tempfile.TemporaryDirectory(
                    prefix="feature-sealed-check-", dir=output
                ) as folder:
                    decrypt(
                        sealed,
                        Path(folder) / "plain",
                        encryption_key,
                        entry["sha256"],
                        checkpoint=checkpoint,
                    )
                metadata = {"sha256": checksum(sealed), "bytes": sealed.stat().st_size}
            else:
                metadata = encrypt(path, sealed, encryption_key, checkpoint=checkpoint)
            key = f"production/catalog-backup/{metadata['sha256']}.bin"
            box.publish_catalog(sealed, key)
            remote["files"].append(
                dict(
                    entry,
                    remote_key=key,
                    cipher_sha256=metadata["sha256"],
                    cipher_bytes=metadata["bytes"],
                )
            )
        else:
            key = f"production/catalog-backup/{entry['sha256']}.jsonl.gz"
            box.publish_catalog(path, key)
            remote["files"].append(dict(entry, remote_key=key))
    path = output / "offsite-features.json"
    if path.exists():
        if (
            path.is_symlink()
            or path.stat().st_size > catalog.ROW_BYTES
            or json.loads(path.read_text()) != remote
        ):
            raise ArchiveBlocked("Retained feature publication manifest differs")
    else:
        catalog.save_json(path, remote)
    reference = {
        "key": f"production/catalog-backup/{checksum(path)}.json",
        "sha256": checksum(path),
        "size": path.stat().st_size,
    }
    checkpoint()
    box.publish_catalog(path, reference["key"])
    readback = output / "readback"
    if readback.exists():
        import uuid

        readback = output / ("readback-" + uuid.uuid4().hex)
    catalog.private_directory(readback)
    returned = readback / "manifest.json"
    reader = verification_box or box
    reader.download_catalog(reference["key"], returned, reference["size"])
    if returned.stat().st_size != reference["size"] or checksum(returned) != reference["sha256"]:
        raise ArchiveBlocked("Feature manifest readback mismatch")
    independent = json.loads(returned.read_text())
    check_manifest(independent)
    files = {}
    for entry in independent["files"]:
        checkpoint()
        local = readback / entry["file"]
        if manifest["format"] == FORMAT_V2:
            from app.tiered_archive.backup import decrypt

            expected = f"production/catalog-backup/{entry['cipher_sha256']}.bin"
            sealed = readback / (entry["file"] + ".bin")
            reader.download_catalog(expected, sealed, entry["cipher_bytes"])
            if (
                sealed.stat().st_size != entry["cipher_bytes"]
                or checksum(sealed) != entry["cipher_sha256"]
            ):
                raise ArchiveBlocked("Encrypted feature readback differs")
            decrypt(sealed, local, encryption_key, entry["sha256"], checkpoint=checkpoint)
        else:
            expected = f"production/catalog-backup/{entry['sha256']}.jsonl.gz"
            if entry.get("remote_key") != expected:
                raise ArchiveBlocked("Unexpected remote feature part")
            reader.download_catalog(expected, local, entry["bytes"])
        files[entry["file"]] = local
    counts = restore(independent, files, readback / "features.sqlite3", checkpoint)
    proof = {
        "format": manifest["format"],
        "status": "verified",
        "created_at": datetime.now(UTC).isoformat(),
        "reference": reference,
        "tables": counts,
        "compressed_bytes": size,
        "storage_box": capacity,
        "deleted_files": 0,
        "deletion_readiness": False,
        "independent_read_identity": verification_box is not None,
    }
    checkpoint()
    catalog.save_json(output / "verified.json", proof)
    return proof


def main():
    from .runner import destination_from_environment, health_probe

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--format-version", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    if os.environ.get("RAW_ARCHIVE_ENVIRONMENT") != "production":
        raise ArchiveBlocked("Production feature backup requires explicit environment")
    if not (
        os.environ.get("RAW_ARCHIVE_COORDINATION_DIR") or os.environ.get("RAW_ARCHIVE_LEASE_HOST")
    ):
        raise ArchiveBlocked("Source archive coordination must be configured")
    # Protect intake regardless of accidentally weaker supplied settings.
    os.environ["RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB"] = str(
        max(640, int(os.environ.get("RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB", "640")))
    )
    os.environ["RAW_ARCHIVE_MAX_HOST_LOAD"] = str(
        min(2.4, float(os.environ.get("RAW_ARCHIVE_MAX_HOST_LOAD", "2.4")))
    )
    if os.environ.get("RAW_ARCHIVE_WORKER_ROLE") == "archive":
        os.environ["RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB"] = str(
            max(640, int(os.environ.get("RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB", "640")))
        )
    probe = health_probe()
    start = time.monotonic()

    def check_lease():
        pass

    def checkpoint():
        check_lease()
        if time.monotonic() - start > 120:
            raise SafetyPause("feature_runtime_budget")
        if not probe():
            raise SafetyPause(probe.last.get("reason", "headroom"), probe.last)
        if (
            args.output.exists()
            and sum(path.stat().st_size for path in args.output.glob("*.jsonl.gz")) > MAX_BYTES
        ):
            raise SafetyPause("feature_spool_budget")
        if args.output.exists() and shutil.disk_usage(args.output).free < 2 * 1024**3:
            raise SafetyPause("feature_local_space")

    try:
        with catalog_lease() as check_lease:
            checkpoint()
            catalog.private_directory(args.output)
            if shutil.disk_usage(args.output).free < MAX_BYTES * 3:
                raise SafetyPause("feature_local_space")
            completed = args.output / "features.json"
            if completed.exists():
                if completed.is_symlink() or completed.stat().st_size > catalog.ROW_BYTES:
                    raise ArchiveBlocked("Private bounded completed feature manifest required")
                manifest = json.loads(completed.read_text())
                check_manifest(manifest)
                for entry in manifest["files"]:
                    checkpoint()
                    path = args.output / entry["file"]
                    if (
                        path.is_symlink()
                        or path.stat().st_size != entry["bytes"]
                        or checksum(path) != entry["sha256"]
                    ):
                        raise ArchiveBlocked("Completed source features changed")
            else:
                manifest = export_features(args.output, checkpoint, version=args.format_version)
                check_manifest(manifest)
                catalog.save_json(completed, manifest)
        # SQL connections and their bounded source lease are closed before transfer.
        # Publication/restore gets a separate two-minute, equally guarded phase.
        start = time.monotonic()
        with catalog_lease() as check_lease:
            checkpoint()
            box = destination_from_environment()
            from .read_only_box import configured_catalog_read_only_box

            verification_box = configured_catalog_read_only_box()
            try:
                if (
                    os.environ.get("RAW_ARCHIVE_WORKER_ROLE") == "archive"
                    and verification_box is None
                ):
                    raise ArchiveBlocked(
                        "Archive feature acceptance requires separate read-only access"
                    )
                with box.session(args.output):
                    encryption_key = None
                    if manifest["format"] == FORMAT_V2:
                        from app.tiered_archive.backup import key_from_file

                        key_path = os.environ.get("TIERED_ARCHIVE_BACKUP_KEY_FILE", "")
                        if not key_path or not Path(key_path).is_absolute():
                            raise ArchiveBlocked("Global feature backup encryption key is required")
                        encryption_key = key_from_file(Path(key_path))
                    proof = publish_and_restore(
                        args.output,
                        manifest,
                        box,
                        checkpoint,
                        verification_box=verification_box,
                        encryption_key=encryption_key,
                    )
            finally:
                box.close()
                if verification_box is not None:
                    verification_box.close()
            print(json.dumps(proof))
    finally:
        probe.close()


def export_features(output, checkpoint, *, version=1):
    from sqlalchemy import create_engine, inspect, text

    from .runner import metadata_source_settings

    if version not in (1, 2):
        raise ArchiveBlocked("Unknown feature export version")
    roster = TABLES if version == 1 else TABLES_V2
    manifest = {
        "schema": version,
        "format": FORMAT if version == 1 else FORMAT_V2,
        "environment": "production",
        "allowlist_sha256": catalog.ALLOWLIST_SHA256
        if version == 1
        else FEATURE_V2_ALLOWLIST_SHA256,
        "created_at": datetime.now(UTC).isoformat(),
        "tables": {},
        "files": [],
    }
    for kind, tables in roster.items():
        engine = create_engine(
            metadata_source_settings(kind, "production")["database"],
            pool_size=1,
            max_overflow=0,
            connect_args={
                "connect_timeout": 5,
                "options": "-c default_transaction_read_only=on -c statement_timeout=8000 -c lock_timeout=150 -c jit=off -c work_mem=2048",
            },
        )
        parts = catalog.Parts(output, kind, checkpoint, compresslevel=1)
        try:
            with (
                engine.connect().execution_options(isolation_level="REPEATABLE READ") as db,
                db.begin(),
            ):
                if db.execute(text("SHOW transaction_read_only")).scalar() != "on":
                    raise ArchiveBlocked("Archive source must be read-only")
                if (
                    kind == "direct"
                    and version == 1
                    and any(inspect(db).has_table(name) for name in FEATURE_V2_COLUMNS)
                ):
                    raise ArchiveBlocked("Direct feature history requires version 2 backup")
                counts = {}
                for table in tables:
                    fields = (
                        FEATURE_V2_COLUMNS[table]
                        if table in FEATURE_V2_COLUMNS
                        else catalog.COLUMNS[kind][table]
                    )
                    checkpoint()
                    statement = (
                        "SELECT "
                        + ",".join('"' + name + '"' for name in fields)
                        + ' FROM "'
                        + table
                        + '"'
                    )
                    rows = (
                        db.execution_options(stream_results=True, yield_per=100)
                        .execute(text(statement))
                        .mappings()
                    )
                    count = 0
                    for row in rows:
                        value = dict(row)
                        parts.write(
                            validate_v2_row(kind, table, value)
                            if version == 2
                            else catalog.validate_row(kind, table, value)
                        )
                        count += 1
                    counts[table] = count
                manifest["tables"][kind] = counts
        finally:
            parts.finish()
            engine.dispose()
        manifest["files"].extend(parts.files)
    return manifest


def export_direct_revision(session, revision, output, checkpoint=lambda: None):
    """Export exactly one current Direct revision; never claims fleet coverage."""
    from .direct_feature_history import read_history

    history = read_history(session, revision)
    parts = catalog.Parts(output, "direct", checkpoint, compresslevel=1)
    try:
        for row in history["results"]:
            checkpoint()
            parts.write(validate_v2_row("direct", "direct_feature_result", row))
    finally:
        parts.finish()
    scope = {key: history[key] for key in ("segment_id", "revision", "manifest_sha256")}
    manifest = {
        "schema": 3,
        "format": FORMAT_DIRECT_REVISION,
        "environment": "production",
        "allowlist_sha256": DIRECT_REVISION_ALLOWLIST_SHA256,
        "created_at": datetime.now(UTC).isoformat(),
        "scientific_coverage": scope,
        "tables": {"direct": {"direct_feature_result": len(history["results"])}},
        "files": parts.files,
    }
    check_manifest(manifest)
    return manifest, history


def compare_restored_direct_history(database, source_history, revision):
    """Compare a fully verified isolated v2 restore with one live source history.

    This comparator does not itself prove the read identity or authorize deletion.
    """
    from .direct_feature_history import validate_history

    expected_digest = validate_history(source_history, revision)
    if not database.is_absolute() or database.is_symlink() or not database.is_file():
        raise ArchiveBlocked("Isolated feature readback database required")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        columns = FEATURE_V2_COLUMNS["direct_feature_result"]
        actual_columns = tuple(
            row[1] for row in db.execute("PRAGMA table_info(restored_direct_feature_result)")
        )
        if actual_columns != columns:
            raise ArchiveBlocked("Typed feature readback schema differs")
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT "
            + ",".join(columns)
            + " FROM restored_direct_feature_result WHERE segment_id=? AND revision=? ORDER BY input_sha256",
            (revision["segment_id"], revision["revision"]),
        ).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["inputs"] = json.loads(value["inputs"])
            value["payload"] = json.loads(value["payload"])
            values.append(value)
    body = {
        key: source_history[key] for key in ("format", "segment_id", "revision", "manifest_sha256")
    }
    body["results"] = values
    from app.services.signal_feature_contract import canonical_sha256

    readback = {**body, "sha256": canonical_sha256(body)}
    if validate_history(readback, revision) != expected_digest:
        raise ArchiveBlocked("Live and restored scientific histories differ")
    return expected_digest


def entrypoint():
    try:
        main()
        return 0
    except SafetyPause as error:
        print(json.dumps({"status": "paused", "reason": error.code, "deleted_files": 0}))
        return 75
    except Exception as error:
        # Database/SFTP exceptions may contain private configuration.
        print(
            json.dumps(
                {"status": "blocked", "error_type": type(error).__name__, "deleted_files": 0}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(entrypoint())
