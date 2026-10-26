"""Partwise catalog publication and independent restoration.

Each phase retains the original small part and runtime limits. Only the final
manifest exposes a catalog; only all restored parts prove complete recovery.
"""

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import catalog_resume, catalog_stages, wav_catalog
from .policy import ArchiveBlocked, checksum
from .read_only_box import CatalogReadOnlyBox


def require_reader(reader):
    if not isinstance(reader, CatalogReadOnlyBox):
        raise ArchiveBlocked("Dedicated read-only subaccount required; upload access forbidden")


def private_json(path):
    catalog_resume.private(path)
    if path.stat().st_size > wav_catalog.ROW_BYTES:
        raise ArchiveBlocked("Bounded private manifest required")
    return json.loads(path.read_text())


def prepare(stages, output, checkpoint=lambda: None):
    if set(stages) != set(catalog_stages.KINDS) or output.exists():
        raise ArchiveBlocked("All complete stages and a new directory required")
    checkpoint()
    completed = {kind: catalog_stages.load_stage(Path(path), kind) for kind, path in stages.items()}
    if not completed["direct"].get("direct_bucket", "").startswith("greenmind-direct-production"):
        raise ArchiveBlocked("Exact production Direct bucket required")
    now = datetime.now(UTC)
    for stage in completed.values():
        begin, end = (datetime.fromisoformat(stage[k]) for k in ("started_at", "finished_at"))
        if not now - timedelta(hours=24) <= begin <= end <= now:
            raise ArchiveBlocked("Fresh completed source snapshots required")
    size = sum(p["bytes"] + p["decoded_bytes"] for s in completed.values() for p in s["files"])
    disk = shutil.disk_usage(output.parent)
    if disk.free < max(2 * 1024**3, (disk.total + 19) // 20) + 2 * size:
        raise ArchiveBlocked("Isolated recovery must preserve disk reserves")
    wav_catalog.private_directory(output)
    manifest = {
        "schema": 2,
        "format": wav_catalog.FORMAT,
        "allowlist_sha256": wav_catalog.ALLOWLIST_SHA256,
        "environment": "production",
        "created_at": min(s["started_at"] for s in completed.values()),
        "direct_bucket": completed["direct"]["direct_bucket"],
        "cross_database_atomic": False,
        "tables": {k: completed[k]["tables"] for k in wav_catalog.COLUMNS},
        "source_windows": {
            k: {n: s[n] for n in ("started_at", "finished_at")} for k, s in completed.items()
        },
        "files": [],
    }
    for kind in catalog_stages.KINDS:
        for entry in completed[kind]["files"]:
            checkpoint()
            os.link(Path(stages[kind]) / entry["file"], output / entry["file"])
            manifest["files"].append(entry)
    wav_catalog.manifest_check(manifest)
    wav_catalog.save_json(output / "manifest.json", manifest)
    return {"status": "prepared", "complete_recovery": False, "deleted_files": 0}


def entry_at(manifest, position):
    wav_catalog.manifest_check(manifest)
    if type(position) is not int or not 0 <= position < len(manifest["files"]):
        raise ArchiveBlocked("Exact manifest part required")
    entry = manifest["files"][position]
    if entry["kind"] not in catalog_stages.KINDS:
        raise ArchiveBlocked("Invalid catalog source")
    return entry


def capacity(box, manifest):
    value = box.capacity()
    required = max(2 * 1024**3, (value["total_bytes"] + 19) // 20)
    required += 2 * sum(e["bytes"] for e in manifest["files"])
    if value["available_bytes"] < required:
        raise ArchiveBlocked("Storage Box reserve is insufficient")


def part_proof(manifest_path, entry, identity):
    return {
        "manifest_sha256": checksum(manifest_path),
        "destination": identity,
        "sha256": entry["sha256"],
        "bytes": entry["bytes"],
        "deleted_files": 0,
    }


def publish_part(manifest_path, position, box, checkpoint=lambda: None):
    manifest = private_json(manifest_path)
    entry = entry_at(manifest, position)
    checkpoint()
    path = manifest_path.parent / entry["file"]
    catalog_resume.private(path)
    if path.stat().st_size != entry["bytes"] or checksum(path) != entry["sha256"]:
        raise ArchiveBlocked("Local catalog part changed")
    proof_path = manifest_path.parent / f"published-{position:06d}.json"
    proof = part_proof(manifest_path, entry, box.identity)
    if proof_path.exists():
        if private_json(proof_path) != proof:
            raise ArchiveBlocked("Part publication proof changed")
        return {"status": "reused", "position": position}
    capacity(box, manifest)
    key = f"production/catalog-backup/{entry['sha256']}.jsonl.gz"
    box.publish_catalog(path, key)
    checkpoint()
    wav_catalog.save_json(proof_path, proof)
    return {"status": "published_part", "position": position, "deleted_files": 0}


def publish_manifest(manifest_path, box, checkpoint=lambda: None):
    manifest = private_json(manifest_path)
    wav_catalog.manifest_check(manifest)
    checkpoint()
    for i, entry in enumerate(manifest["files"]):
        if private_json(manifest_path.parent / f"published-{i:06d}.json") != part_proof(
            manifest_path, entry, box.identity
        ):
            raise ArchiveBlocked("Every exact catalog part must be published first")
    capacity(box, manifest)
    remote = dict(
        manifest,
        destination=box.identity,
        files=[
            dict(e, remote_key=f"production/catalog-backup/{e['sha256']}.jsonl.gz")
            for e in manifest["files"]
        ],
    )
    raw = wav_catalog.raw_json(remote)
    if len(raw) > wav_catalog.ROW_BYTES:
        raise ArchiveBlocked("Bounded manifest required")
    digest = hashlib.sha256(raw).hexdigest()
    path = manifest_path.parent / (digest + "-offsite.json")
    if not path.exists():
        wav_catalog.save_json(path, remote)
    if path.is_symlink() or path.read_bytes() != raw:
        raise ArchiveBlocked("Existing remote manifest changed")
    key = f"production/catalog-backup/{digest}.json"
    box.publish_catalog(path, key)
    checkpoint()
    reference = {"key": key, "sha256": digest, "size": len(raw)}
    proof_path = manifest_path.parent / "publication.json"
    if not proof_path.exists():
        wav_catalog.save_json(proof_path, reference)
    elif private_json(proof_path) != reference:
        raise ArchiveBlocked("Publication reference changed")
    return reference


def read_manifest(reader, reference, output, checkpoint=lambda: None):
    require_reader(reader)
    checkpoint()
    if output.exists():
        raise ArchiveBlocked("Independent readback needs a new directory")
    wav_catalog.private_directory(output)
    path = output / "manifest.json"
    reader.download_catalog(reference["key"], path, reference["size"])
    catalog_resume.private(path)
    if path.stat().st_size != reference["size"] or checksum(path) != reference["sha256"]:
        raise ArchiveBlocked("Independent manifest readback differs")
    manifest = private_json(path)
    wav_catalog.manifest_check(manifest)
    if (
        manifest.get("environment") != "production"
        or manifest.get("destination") != reader.identity
    ):
        raise ArchiveBlocked("Independent reader identity differs")
    checkpoint()
    return {"status": "manifest_read_back", "deleted_files": 0}


def restore_part(output, position, reader, checkpoint=lambda: None):
    require_reader(reader)
    manifest_path = output / "manifest.json"
    manifest = private_json(manifest_path)
    entry = entry_at(manifest, position)
    if manifest.get("destination") != reader.identity:
        raise ArchiveBlocked("Separate reader destination differs")
    checkpoint()
    path = output / f"readback-{position:06d}-{os.urandom(8).hex()}.gz"
    # Every attempt independently reads remote bytes, including resumed parts.
    reader.download_catalog(entry["remote_key"], path, entry["bytes"])
    result = catalog_resume.restore_part(
        manifest, position, path, output / "wav-catalog.sqlite3", checkpoint
    )
    proof = dict(
        part_proof(manifest_path, entry, reader.identity),
        position=position,
        reader_kind="dedicated_read_only_subaccount",
    )
    proof_path = output / f"readback-{position:06d}.json"
    if not proof_path.exists():
        wav_catalog.save_json(proof_path, proof)
    elif private_json(proof_path) != proof:
        raise ArchiveBlocked("Independent part evidence changed")
    return result


def finish(output, reference, reader, checkpoint=lambda: None):
    require_reader(reader)
    path = output / "manifest.json"
    manifest = private_json(path)
    if checksum(path) != reference["sha256"] or path.stat().st_size != reference["size"]:
        raise ArchiveBlocked("Final manifest differs from remote reference")
    if manifest.get("destination") != reader.identity:
        raise ArchiveBlocked("Independent reader destination differs")
    for i, entry in enumerate(manifest["files"]):
        expected = dict(
            part_proof(path, entry, reader.identity),
            position=i,
            reader_kind="dedicated_read_only_subaccount",
        )
        if private_json(output / f"readback-{i:06d}.json") != expected:
            raise ArchiveBlocked("Every independent readback proof is required")
        checkpoint()
    proof = catalog_resume.finish(
        manifest, output / "wav-catalog.sqlite3", reference, checkpoint=checkpoint
    )
    proof["independent_read_identity"] = True
    wav_catalog.save_json(output / "verified.json", proof)
    return proof
