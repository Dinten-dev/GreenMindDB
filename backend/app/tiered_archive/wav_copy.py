"""Resume one immutable revision across bounded WAV transfer batches.

Unfinished transfers have no published/verified bundle. The scientific metadata
and the original revision boundary stay unchanged, including fragmented Direct
captures. Only the final complete independent readback can publish the bundle.
"""

import tempfile
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked, Recording

from . import parquet
from .codec import decode, digest, encode
from .pipeline import candidate_json, complete_staged_wav, key_for, verify_file
from .storage import MAX_BYTES, disk_reserve

MAX_PLANS = 5000
MAX_PLAN_BYTES = 2 * 1024**2


def initialize(db):
    db.executescript("""
      CREATE TABLE IF NOT EXISTS wav_copy (
        candidate TEXT PRIMARY KEY, source TEXT NOT NULL, reference BLOB NOT NULL,
        metadata_digest TEXT NOT NULL, bindings TEXT NOT NULL, destination TEXT NOT NULL,
        plan BLOB, done BLOB NOT NULL, completed INTEGER NOT NULL DEFAULT 0);
      CREATE INDEX IF NOT EXISTS wav_copy_reference ON wav_copy
        (source,reference,metadata_digest,bindings,destination,completed);
    """)


def summary(db):
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wav_copy'"
    ).fetchone():
        return {"incomplete_revisions": 0, "copied_wavs": 0}
    rows = list(db.execute("SELECT done FROM wav_copy WHERE completed=0"))
    return {
        "incomplete_revisions": len(rows),
        "copied_wavs": sum(len(decode(row[0])) for row in rows),
    }


def _metadata(adapter, reference):
    envelope, headers, flags = adapter.metadata(reference)
    raw = encode({"envelope": envelope, "headers": headers, "flags": flags})
    if len(raw) > MAX_PLAN_BYTES:
        raise ArchiveBlocked("WAV scientific metadata exceeds copy-plan budget")
    return envelope, headers, flags, digest(raw)


def advance(
    adapter,
    reference,
    store,
    ledger,
    *,
    bindings,
    checkpoint,
    maximum_wavs,
    maximum_bytes,
    can_continue=lambda: True,
    maximum_object_bytes=None,
    closed_before=None,
):
    if (
        type(maximum_wavs) is not int
        or not 1 <= maximum_wavs <= 127
        or type(maximum_bytes) is not int
        or not 1 <= maximum_bytes <= MAX_BYTES
    ):
        raise ArchiveBlocked("Bounded WAV transfer invocation required")
    if maximum_object_bytes is not None and (
        type(maximum_object_bytes) is not int or not 1 <= maximum_object_bytes <= MAX_BYTES
    ):
        raise ArchiveBlocked("Bounded WAV object ceiling required")
    if closed_before is not None:
        from .policy import utc

        closed_before = utc(closed_before)
    checkpoint()
    initialize(ledger.db)
    base, headers, flags, metadata_digest = _metadata(adapter, reference)
    for item in base["body"]["originals"]:
        record = Recording.from_receipt(item["recording"])
        if maximum_object_bytes is not None and record.size > maximum_object_bytes:
            raise ArchiveBlocked("One WAV exceeds the exact pilot byte ceiling")
        if closed_before is not None and (
            record.ended_at >= closed_before or record.received_at >= closed_before
        ):
            raise ArchiveBlocked("Exact WAV pilot requires the same closed-day source boundary")
    scope = (adapter.kind, encode(reference), metadata_digest, bindings, store.identity)
    old = ledger.db.execute(
        "SELECT candidate FROM wav_copy WHERE source=? AND reference=? AND metadata_digest=? "
        "AND bindings=? AND destination=? AND completed=1",
        scope,
    ).fetchone()
    if old:
        receipt = ledger.get(old[0])
        if not receipt or receipt.get("destination") != store.identity:
            raise ArchiveBlocked("Completed WAV plan lost its published receipt")
        # A process can stop after publication but before legacy URL bindings.
        # Recover the exact archived envelope without touching source bodies.
        with tempfile.TemporaryDirectory(dir=ledger.root) as directory:
            path = Path(directory) / "metadata.parquet"
            primary = receipt["files"][0]
            store.download(primary["key"], path, primary["bytes"])
            verify_file(path, primary, checkpoint)
            rows = list(parquet.read(path, primary, checkpoint=checkpoint))
            if len(rows) != 1:
                raise ArchiveBlocked("Completed WAV metadata requires one envelope")
            envelope = rows[0]
            scientific = decode(encode(envelope))
            for item in scientific["body"]["originals"]:
                item.pop("snapshot", None)
            if encode(scientific) != encode(base):
                raise ArchiveBlocked("Completed WAV scientific metadata changed")
        return {
            "complete": True,
            "receipt": receipt,
            "envelope": envelope,
            "copied_wavs": 0,
            "copied_bytes": 0,
        }
    envelope = decode(encode(base))
    originals = envelope["body"]["originals"]
    if not 1 <= len(originals) <= 1024:
        raise ArchiveBlocked("Bounded complete WAV revision required")
    for item in originals:
        checkpoint()
        item["snapshot"] = adapter.objects.snapshot(Recording.from_receipt(item["recording"]))
    with tempfile.TemporaryDirectory(dir=ledger.root) as directory:
        folder = Path(directory)
        path = folder / "metadata.parquet"
        metadata = dict(parquet.write(path, [envelope], checkpoint=checkpoint), format="parquet")
        candidate = adapter.candidate(envelope, metadata, flags)
        candidate.validate()
        metadata["key"] = key_for(candidate, metadata["sha256"], "parquet")
        references = [metadata]
        for item in originals:
            record = Recording.from_receipt(item["recording"])
            references.append(
                {
                    "format": "wav",
                    "key": record.remote_key(candidate.environment),
                    "sha256": record.sha256,
                    "bytes": record.size,
                    "wav_header": headers[record.key],
                }
            )
        if sum(reference["bytes"] for reference in references[1:]) > MAX_BYTES:
            raise ArchiveBlocked("Whole WAV revision exceeds existing byte ceiling")
        plan = encode(
            {"envelope": envelope, "candidate": candidate_json(candidate), "files": references}
        )
        if len(plan) > MAX_PLAN_BYTES:
            raise ArchiveBlocked("WAV copy plan exceeds metadata budget")
        previous = ledger.get(candidate.identity)
        if previous:
            if previous.get("destination") != store.identity or previous.get("files") != references:
                raise ArchiveBlocked("Published WAV bundle changed")
            with ledger.db:
                ledger.db.execute(
                    "INSERT OR REPLACE INTO wav_copy VALUES(?,?,?,?,?,?,NULL,?,1)",
                    (candidate.identity, *scope, encode([])),
                )
            return {
                "complete": True,
                "receipt": previous,
                "envelope": envelope,
                "copied_wavs": 0,
                "copied_bytes": 0,
            }
        existing = ledger.db.execute(
            "SELECT plan,done,completed,source,reference,metadata_digest,bindings,destination "
            "FROM wav_copy WHERE candidate=?",
            (candidate.identity,),
        ).fetchone()
        if existing:
            if existing[0] != plan or existing[2] or existing[3:] != scope:
                raise ArchiveBlocked("Durable WAV copy plan differs")
            done = set(decode(existing[1]))
            if not done <= set(range(len(originals))):
                raise ArchiveBlocked("Invalid completed WAV transfer indexes")
        else:
            if (
                ledger.db.execute("SELECT COUNT(*) FROM wav_copy WHERE completed=0").fetchone()[0]
                >= MAX_PLANS
            ):
                raise ArchiveBlocked("Incomplete WAV copy queue full")
            with ledger.db:
                ledger.db.execute(
                    "INSERT INTO wav_copy VALUES(?,?,?,?,?,?,?,?,0)",
                    (candidate.identity, *scope, plan, encode([])),
                )
            done = set()
        store.publish(metadata["key"], path)
        copied = copied_bytes = 0
        for index, item in enumerate(originals):
            if index in done:
                continue
            record = Recording.from_receipt(item["recording"])
            if (
                copied >= maximum_wavs
                or copied_bytes + record.size > maximum_bytes
                or not can_continue()
            ):
                break
            checkpoint()
            target = folder / "original.wav"
            try:
                disk_reserve(folder, record.size * 2)
                adapter.objects.download(record, item["snapshot"], target)
                verify_file(target, references[index + 1], checkpoint)
                store.publish(references[index + 1]["key"], target)
            finally:
                target.unlink(missing_ok=True)
            # SFTP PUT/independent readback completes before durable advancement.
            done.add(index)
            with ledger.db:
                ledger.db.execute(
                    "UPDATE wav_copy SET done=? WHERE candidate=?",
                    (encode(sorted(done)), candidate.identity),
                )
            copied += 1
            copied_bytes += record.size
        receipt = None
        if len(done) == len(originals):
            checkpoint()
            if _metadata(adapter, reference)[3] != metadata_digest:
                raise ArchiveBlocked("Scientific source changed during staged WAV copy")
            for item in originals:
                checkpoint()
                record = Recording.from_receipt(item["recording"])
                if adapter.objects.snapshot(record) != item["snapshot"]:
                    raise ArchiveBlocked("Source object version changed during staged WAV copy")
            receipt = complete_staged_wav(
                candidate, references, store, ledger, checkpoint=checkpoint
            )
            with ledger.db:
                # Keep the small source/candidate index; full metadata is now archived.
                ledger.db.execute(
                    "UPDATE wav_copy SET completed=1,plan=NULL,done=? WHERE candidate=?",
                    (encode([]), candidate.identity),
                )
        return {
            "complete": receipt is not None,
            "receipt": receipt,
            "envelope": envelope if receipt else None,
            "copied_wavs": copied,
            "copied_bytes": copied_bytes,
            "candidate": candidate.identity,
            "remaining_wavs": len(originals) - len(done),
        }
