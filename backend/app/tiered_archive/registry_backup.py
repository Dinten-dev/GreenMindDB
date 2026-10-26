"""Rebuild the archive catalogue without either application's database."""

import gzip
import json
import os
import tempfile
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked, checksum

from .codec import decode, encode
from .pipeline import key_for
from .policy import POLICY_SHA256
from .runtime import candidate_from_json


def backup(ledger, store, *, checkpoint):
    ledger.db.execute("CREATE TABLE IF NOT EXISTS catalog_sent(key TEXT PRIMARY KEY)")
    ledger.db.commit()
    references = []
    buffer = []
    size = 0
    template = None
    with tempfile.TemporaryDirectory(dir=ledger.root) as folder:
        folder = Path(folder)

        def flush():
            nonlocal buffer, size
            if not buffer:
                return
            path = folder / (str(len(references)) + ".gz")
            path.write_bytes(gzip.compress(b"\n".join(buffer) + b"\n", compresslevel=6, mtime=0))
            os.chmod(path, 0o600)
            reference = {
                "key": key_for(template, checksum(path), "gz"),
                "sha256": checksum(path),
                "bytes": path.stat().st_size,
                "encoding": "gzip-jsonl-v1",
            }
            checkpoint()
            if not ledger.db.execute(
                "SELECT 1 FROM catalog_sent WHERE key=?", (reference["key"],)
            ).fetchone():
                store.publish(reference["key"], path)
                with ledger.db:
                    ledger.db.execute("INSERT INTO catalog_sent VALUES(?)", (reference["key"],))
            references.append(reference)
            buffer = []
            size = 0

        for (raw,) in ledger.db.execute("SELECT receipt FROM bundle ORDER BY id"):
            receipt = json.loads(raw)
            candidate = candidate_from_json(receipt["source"])
            if template is None:
                from dataclasses import replace

                template = replace(
                    candidate,
                    organization="00000000-0000-0000-0000-000000000000",
                    dataset="archive_catalog",
                )
            body = encode({"type": "bundle", "receipt": receipt})
            if size + len(body) > 1024 * 1024:
                flush()
            buffer.append(body)
            size += len(body) + 1
        if template is None:
            return {"status": "empty"}
        for ordinal, at, candidate, state, body in ledger.db.execute(
            "SELECT ordinal,at,candidate,state,body FROM event ORDER BY ordinal"
        ):
            row = encode(
                {
                    "type": "event",
                    "ordinal": ordinal,
                    "at": at,
                    "candidate": candidate,
                    "state": state,
                    "body": body,
                }
            )
            if size + len(row) > 1024 * 1024:
                flush()
            buffer.append(row)
            size += len(row) + 1
        flush()
        index = folder / "index.json"
        index.write_text(
            json.dumps({"schema": 1, "policy": POLICY_SHA256, "parts": references}, sort_keys=True)
        )
        os.chmod(index, 0o600)
        if index.stat().st_size > 1024 * 1024:
            raise ArchiveBlocked("Catalogue root exceeds budget")
        reference = {
            "key": key_for(template, checksum(index), "json"),
            "sha256": checksum(index),
            "bytes": index.stat().st_size,
        }
        checkpoint()
        store.publish(reference["key"], index)
        return reference


def restore(reference, ledger, store, *, checkpoint):
    if ledger.status():
        raise ArchiveBlocked("Catalogue restore requires empty isolated state")
    with tempfile.TemporaryDirectory(dir=ledger.root) as folder:
        path = Path(folder) / "index"
        store.download(reference["key"], path, reference["bytes"])
        if checksum(path) != reference["sha256"]:
            raise ArchiveBlocked("Catalogue root checksum mismatch")
        index = json.loads(path.read_text())
        if (
            index.get("schema") != 1
            or index.get("policy") != POLICY_SHA256
            or len(index["parts"]) > 65536
        ):
            raise ArchiveBlocked("Unknown catalogue format")
        count = 0
        with ledger.db:
            for number, part in enumerate(index["parts"]):
                checkpoint()
                target = Path(folder) / str(number)
                if not 0 < part["bytes"] <= 1024 * 1024 + 1:
                    raise ArchiveBlocked("Oversized catalogue part")
                store.download(part["key"], target, part["bytes"])
                if target.stat().st_size != part["bytes"] or checksum(target) != part["sha256"]:
                    raise ArchiveBlocked("Catalogue part checksum mismatch")
                if part.get("encoding") not in {None, "gzip-jsonl-v1"}:
                    raise ArchiveBlocked("Unknown catalogue compression")
                opener = gzip.open if part.get("encoding") == "gzip-jsonl-v1" else open
                with opener(target, "rb") as body:
                    decoded = 0
                    while raw := body.readline(1024 * 1024 + 2):
                        decoded += len(raw)
                        if len(raw) > 1024 * 1024 + 1 or decoded > 2 * 1024 * 1024:
                            raise ArchiveBlocked("Catalogue decoding budget exceeded")
                        item = decode(raw)
                        if item["type"] == "bundle":
                            receipt = item["receipt"]
                            candidate = candidate_from_json(receipt["source"])
                            if (
                                receipt["candidate"] != candidate.identity
                                or receipt["policy"] != POLICY_SHA256
                            ):
                                raise ArchiveBlocked("Catalogue source identity mismatch")
                            ledger.db.execute(
                                "INSERT INTO bundle VALUES(?,?,?,?,?,?,?,?)",
                                (
                                    candidate.identity,
                                    candidate.environment,
                                    candidate.source,
                                    candidate.organization,
                                    candidate.dataset,
                                    candidate.start.isoformat(),
                                    candidate.end.isoformat(),
                                    json.dumps(receipt, sort_keys=True),
                                ),
                            )
                            count += 1
                        elif item["type"] == "event":
                            ledger.db.execute(
                                "INSERT INTO event VALUES(?,?,?,?,?)",
                                (
                                    item["ordinal"],
                                    item["at"],
                                    item["candidate"],
                                    item["state"],
                                    item["body"],
                                ),
                            )
                        else:
                            raise ArchiveBlocked("Unknown catalogue record")
                target.unlink()
    ledger.publish_read_view()
    return {"restored_bundles": count, "source_accesses": 0}
