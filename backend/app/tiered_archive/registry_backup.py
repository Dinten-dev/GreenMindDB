"""Rebuild the archive catalogue without either application's database."""

import gzip
import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked, checksum

from . import registry_v5
from .codec import decode, encode
from .pipeline import key_for
from .policy import POLICY_SHA256
from .runtime import candidate_from_json


def initialize_managed(db):
    db.execute("""CREATE TABLE IF NOT EXISTS managed_snapshot (
        intent TEXT PRIMARY KEY, state TEXT NOT NULL, catalog TEXT NOT NULL,
        snapshot TEXT, proof TEXT, due TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS managed_snapshot_action (
        intent TEXT NOT NULL, operation TEXT NOT NULL, action_id INTEGER NOT NULL,
        PRIMARY KEY(intent, operation))""")


def backup(ledger, store, *, checkpoint):
    if ledger.db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='science_registry_import_intent'"
    ).fetchone():
        if (
            ledger.db.execute("SELECT count(*) FROM science_registry_import_intent").fetchone()[0]
            > 1
            or ledger.db.execute(
                "SELECT 1 FROM science_registry_import_intent WHERE state IS NULL OR state NOT IN ('applied','complete') OR (state='complete' AND backup_reference IS NULL) LIMIT 1"
            ).fetchone()
        ):
            raise ArchiveBlocked("Incomplete science registry import blocks backup")
    for transition in ("science_v3_reconcile", "sql_deletion_transition"):
        if ledger.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (transition,)
        ).fetchone():
            raise ArchiveBlocked("Scope reconciliation is in progress")
    ledger.db.execute("CREATE TABLE IF NOT EXISTS catalog_sent(key TEXT PRIMARY KEY)")
    ledger.db.commit()
    references = []
    buffer = []
    size = 0
    template = None
    copy_indexes = False
    managed_indexes = False
    pin_coverage = False
    v5_present = any(
        ledger.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        for name in registry_v5.TABLES
    )
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
            if v5_present:
                returned = folder / ("v5-returned-" + str(len(references)))
                store.download(reference["key"], returned, reference["bytes"])
                if (
                    returned.stat().st_size != reference["bytes"]
                    or checksum(returned) != reference["sha256"]
                ):
                    raise ArchiveBlocked("V5 catalogue part independent readback differs")
                returned.unlink()
            references.append(reference)
            buffer = []
            size = 0

        for (raw,) in ledger.db.execute("SELECT receipt FROM bundle ORDER BY id"):
            receipt = json.loads(raw)
            candidate = candidate_from_json(receipt["source"])
            if template is not None and candidate.environment != template.environment:
                raise ArchiveBlocked("Mixed archive environments cannot share a catalogue")
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
            for table in registry_v5.TABLES:
                if ledger.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone():
                    registry_v5.schema(ledger.db, table)
                    if ledger.db.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchone():
                        raise ArchiveBlocked(
                            "Cannot omit nonempty science/SQL journals from empty catalogue"
                        )
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
        if ledger.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wav_copy'"
        ).fetchone():
            for (
                candidate,
                source,
                reference,
                metadata_digest,
                bindings,
                destination,
            ) in ledger.db.execute(
                "SELECT candidate,source,reference,metadata_digest,bindings,destination "
                "FROM wav_copy WHERE completed=1 ORDER BY candidate"
            ):
                checkpoint()
                receipt = ledger.get(candidate)
                if (
                    not receipt
                    or receipt["destination"] != destination
                    or receipt["source"]["source"] != source
                ):
                    raise ArchiveBlocked("Completed WAV copy index differs from catalogue")
                row = encode(
                    {
                        "type": "wav_copy",
                        "candidate": candidate,
                        "source": source,
                        "reference": decode(reference),
                        "metadata_digest": metadata_digest,
                        "bindings": bindings,
                        "destination": destination,
                    }
                )
                if size + len(row) > 1024 * 1024:
                    flush()
                buffer.append(row)
                size += len(row) + 1
                copy_indexes = True
        if ledger.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='managed_snapshot'"
        ).fetchone():
            for row in ledger.db.execute(
                "SELECT intent,state,catalog,snapshot,proof,due FROM managed_snapshot ORDER BY intent"
            ):
                checkpoint()
                item = encode({"type": "managed_snapshot", "values": list(row)})
                if size + len(item) > 1024 * 1024:
                    flush()
                buffer.append(item)
                size += len(item) + 1
                managed_indexes = True
        if ledger.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='managed_snapshot_action'"
        ).fetchone():
            for row in ledger.db.execute(
                "SELECT intent,operation,action_id FROM managed_snapshot_action ORDER BY intent,operation"
            ):
                checkpoint()
                item = encode({"type": "managed_snapshot_action", "values": list(row)})
                if size + len(item) > 1024 * 1024:
                    flush()
                buffer.append(item)
                size += len(item) + 1
                managed_indexes = True
        if ledger.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='managed_snapshot_scope'"
        ).fetchone():
            scope = ledger.db.execute(
                "SELECT id,epoch,contract_sha256,recovery_state FROM managed_snapshot_scope WHERE id=1"
            ).fetchone()
            if scope:
                for kind, rows in (
                    ("managed_snapshot_scope", [scope]),
                    (
                        "managed_snapshot_epoch",
                        ledger.db.execute(
                            "SELECT intent,epoch FROM managed_snapshot_epoch ORDER BY intent"
                        ),
                    ),
                    (
                        "managed_snapshot_pin",
                        ledger.db.execute(
                            "SELECT id,intent,snapshot_id,kind,operation_sha256,owner,state,acquired_at,completed_at,completion_sha256 FROM managed_snapshot_pin ORDER BY id"
                        ),
                    ),
                    (
                        "managed_snapshot_pin_receipt",
                        ledger.db.execute(
                            "SELECT pin_id,receipt,receipt_sha256 FROM managed_snapshot_pin_receipt ORDER BY pin_id"
                        ),
                    ),
                    (
                        "managed_snapshot_pin_event",
                        ledger.db.execute(
                            "SELECT ordinal,pin_id,state,at,evidence FROM managed_snapshot_pin_event ORDER BY ordinal"
                        ),
                    ),
                ):
                    for row in rows:
                        checkpoint()
                        item = encode({"type": kind, "values": list(row)})
                        if size + len(item) > 1024 * 1024:
                            flush()
                        buffer.append(item)
                        size += len(item) + 1
                pin_coverage = True
        extensions = {}
        for table in registry_v5.TABLES:
            exists = bool(
                ledger.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
            )
            count = 0
            if exists:
                registry_v5.schema(ledger.db, table)
                for values in ledger.db.execute("SELECT * FROM " + table + " ORDER BY 1,2"):
                    checkpoint()
                    values = list(values)
                    registry_v5.validate(table, values, template.environment, ledger)
                    item = encode({"type": table, "values": values})
                    if size + len(item) > 1024 * 1024:
                        flush()
                    buffer.append(item)
                    size += len(item) + 1
                    count += 1
                    if count > 100000:
                        raise ArchiveBlocked("V5 journal row budget exceeded")
            extensions[table] = {"present": exists, "rows": count}
        flush()
        index = folder / "index.json"
        index.write_text(
            json.dumps(
                {
                    "schema": 5
                    if any(item["present"] for item in extensions.values())
                    else 4
                    if pin_coverage
                    else 3
                    if managed_indexes
                    else 2
                    if copy_indexes
                    else 1,
                    "policy": POLICY_SHA256,
                    "environment": template.environment,
                    "parts": references,
                    **(
                        {
                            "journal_v5": {
                                "version": 1,
                                "tables": extensions,
                                "managed_pin_coverage": pin_coverage,
                            }
                        }
                        if any(item["present"] for item in extensions.values())
                        else {}
                    ),
                },
                sort_keys=True,
            )
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


def restore(reference, ledger, store, *, checkpoint, expected_environment):
    if expected_environment not in {"staging", "production"} or not reference["key"].startswith(
        expected_environment + "/"
    ):
        raise ArchiveBlocked("Catalogue environment differs from restore target")
    if ledger.status():
        raise ArchiveBlocked("Catalogue restore requires empty isolated state")
    with tempfile.TemporaryDirectory(dir=ledger.root) as folder:
        path = Path(folder) / "index"
        store.download(reference["key"], path, reference["bytes"])
        if checksum(path) != reference["sha256"]:
            raise ArchiveBlocked("Catalogue root checksum mismatch")
        index = json.loads(path.read_text())
        if (
            index.get("schema") not in {1, 2, 3, 4, 5}
            or index.get("policy") != POLICY_SHA256
            or index.get("environment", expected_environment) != expected_environment
            or len(index["parts"]) > 65536
        ):
            raise ArchiveBlocked("Unknown catalogue format")
        extension = index.get("journal_v5")
        if index["schema"] == 5:
            from .storage import validate_key

            validate_key(reference["key"])
            if not registry_v5.sha(reference["sha256"]) or not reference["key"].endswith(
                "/" + reference["sha256"] + ".json"
            ):
                raise ArchiveBlocked("V5 catalogue root identity differs")
            if (
                type(extension) is not dict
                or set(extension) != {"version", "tables", "managed_pin_coverage"}
                or extension["version"] != 1
                or type(extension["managed_pin_coverage"]) is not bool
                or type(extension["tables"]) is not dict
                or not (
                    set(registry_v5.TABLES)
                    - {
                        "sql_deletion_epoch",
                        "science_v3_import_provenance",
                        "sql_audit_v3_backup",
                        "sql_audit_v3_backup_history",
                        "sql_audit_v3_scope",
                        "sql_audit_v3_scope_proof",
                    }
                    <= set(extension["tables"])
                    <= set(registry_v5.TABLES)
                )
            ):
                raise ArchiveBlocked("Exact V5 journal coverage required")
            # Older V5 indexes predate SQL scope provenance. Missing provenance
            # remains absent and restore still forces SQL needs_reconcile.
            if "sql_deletion_epoch" not in extension["tables"]:
                extension["tables"]["sql_deletion_epoch"] = {"present": False, "rows": 0}
            if "science_v3_import_provenance" not in extension["tables"]:
                extension["tables"]["science_v3_import_provenance"] = {"present": False, "rows": 0}
            if "sql_audit_v3_backup" not in extension["tables"]:
                extension["tables"]["sql_audit_v3_backup"] = {"present": False, "rows": 0}
            if "sql_audit_v3_backup_history" not in extension["tables"]:
                extension["tables"]["sql_audit_v3_backup_history"] = {"present": False, "rows": 0}
            if "sql_audit_v3_scope" not in extension["tables"]:
                extension["tables"]["sql_audit_v3_scope"] = {"present": False, "rows": 0}
            if "sql_audit_v3_scope_proof" not in extension["tables"]:
                extension["tables"]["sql_audit_v3_scope_proof"] = {"present": False, "rows": 0}
            for value in extension["tables"].values():
                if (
                    type(value) is not dict
                    or set(value) != {"present", "rows"}
                    or type(value["present"]) is not bool
                    or type(value["rows"]) is not int
                    or not 0 <= value["rows"] <= 100000
                    or (not value["present"] and value["rows"] != 0)
                ):
                    raise ArchiveBlocked("Invalid V5 journal coverage")
        elif extension is not None:
            raise ArchiveBlocked("Unexpected V5 journal coverage")
        has_pins = index["schema"] == 4 or (
            index["schema"] == 5 and extension["managed_pin_coverage"]
        )
        restored_v5 = {name: 0 for name in registry_v5.TABLES}
        for name in registry_v5.TABLES:
            existing = ledger.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            if existing:
                registry_v5.schema(ledger.db, name)
                if ledger.db.execute("SELECT 1 FROM " + name + " LIMIT 1").fetchone():
                    raise ArchiveBlocked("Catalogue restore requires empty science/SQL journals")
            if index["schema"] == 5 and extension["tables"][name]["present"]:
                ledger.db.execute(registry_v5.DDL[name])
                registry_v5.schema(ledger.db, name)
        # Legacy indexes cannot establish that the pilot budget was unused.
        for name in ("sql_deletion_scope", "science_v3_scope"):
            ledger.db.execute(
                "CREATE TABLE IF NOT EXISTS "
                + name
                + " (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL)"
            )
            ledger.db.execute(
                "INSERT INTO "
                + name
                + " VALUES (1,'needs_reconcile') ON CONFLICT(id) DO UPDATE SET state='needs_reconcile'"
            )
        ledger.db.commit()
        if ledger.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wav_copy'"
        ).fetchone():
            if ledger.db.execute("SELECT 1 FROM wav_copy LIMIT 1").fetchone():
                raise ArchiveBlocked("Catalogue restore requires empty WAV transfer index")
        if index["schema"] in {2, 3, 4, 5}:
            from .wav_copy import initialize

            initialize(ledger.db)
        if index["schema"] in {3, 4, 5}:
            initialize_managed(ledger.db)
            if ledger.db.execute("SELECT 1 FROM managed_snapshot LIMIT 1").fetchone():
                raise ArchiveBlocked("Catalogue restore requires empty managed snapshot journal")
        if has_pins:
            from .ledger import initialize_snapshot_pins

            initialize_snapshot_pins(ledger.db)
            if ledger.db.execute("SELECT 1 FROM managed_snapshot_scope").fetchone():
                raise ArchiveBlocked("Catalogue restore requires empty snapshot pin scope")
        count = 0
        with ledger.db:
            for number, part in enumerate(index["parts"]):
                if not part["key"].startswith(expected_environment + "/"):
                    raise ArchiveBlocked("Catalogue part environment differs")
                if index["schema"] == 5:
                    validate_key(part["key"])
                    if (
                        set(part) != {"key", "sha256", "bytes", "encoding"}
                        or not registry_v5.sha(part["sha256"])
                        or not part["key"].endswith("/" + part["sha256"] + ".gz")
                        or part["encoding"] != "gzip-jsonl-v1"
                        or type(part["bytes"]) is not int
                    ):
                        raise ArchiveBlocked("V5 catalogue part identity differs")
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
                                or candidate.environment != expected_environment
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
                        elif item["type"] == "wav_copy" and index["schema"] in {2, 3, 4, 5}:
                            receipt = ledger.get(item["candidate"])
                            reference = item["reference"]
                            if (
                                not receipt
                                or receipt["source"]["kind"] != "wav"
                                or receipt["source"]["source"] != item["source"]
                                or receipt["destination"] != item["destination"]
                                or not re.fullmatch("[a-f0-9]{64}", item["metadata_digest"])
                                or not re.fullmatch("[a-f0-9]{64}", item["bindings"])
                                or not isinstance(reference, list)
                                or len(reference) != (1 if item["source"] == "gateway" else 2)
                                or item["source"] not in {"gateway", "direct"}
                            ):
                                raise ArchiveBlocked("Invalid restored WAV transfer identity")
                            uuid.UUID(reference[0])
                            if item["source"] == "direct" and (
                                type(reference[1]) is not int or reference[1] < 1
                            ):
                                raise ArchiveBlocked("Invalid restored WAV revision")
                            ledger.db.execute(
                                "INSERT INTO wav_copy VALUES(?,?,?,?,?,?,NULL,?,1)",
                                (
                                    item["candidate"],
                                    item["source"],
                                    encode(reference),
                                    item["metadata_digest"],
                                    item["bindings"],
                                    item["destination"],
                                    encode([]),
                                ),
                            )
                        elif item["type"] == "managed_snapshot" and index["schema"] in {3, 4, 5}:
                            values = item.get("values")
                            if (
                                type(values) is not list
                                or len(values) != 6
                                or type(values[0]) is not str
                                or not re.fullmatch(r"[a-f0-9]{32}", values[0])
                                or values[1]
                                not in {"intent", "bound", "verified", "delete_intent", "deleted"}
                                or type(values[2]) is not str
                                or any(
                                    value is not None and type(value) is not str
                                    for value in values[3:]
                                )
                            ):
                                raise ArchiveBlocked("Invalid restored managed snapshot journal")
                            try:
                                catalog = json.loads(values[2])
                                snapshot = json.loads(values[3]) if values[3] else None
                                proof = json.loads(values[4]) if values[4] else None
                                if (
                                    type(catalog) is not dict
                                    or (snapshot is not None and type(snapshot) is not dict)
                                    or (proof is not None and type(proof) is not dict)
                                ):
                                    raise ValueError
                            except (TypeError, ValueError):
                                raise ArchiveBlocked(
                                    "Invalid restored managed snapshot evidence"
                                ) from None
                            ledger.db.execute(
                                "INSERT INTO managed_snapshot VALUES(?,?,?,?,?,?)", values
                            )
                        elif item["type"] == "managed_snapshot_action" and index["schema"] in {
                            3,
                            4,
                            5,
                        }:
                            values = item.get("values")
                            if (
                                type(values) is not list
                                or len(values) != 3
                                or type(values[0]) is not str
                                or not re.fullmatch(r"[a-f0-9]{32}", values[0])
                                or values[1] not in {"create", "delete"}
                                or type(values[2]) is not int
                                or values[2] <= 0
                                or not ledger.db.execute(
                                    "SELECT 1 FROM managed_snapshot WHERE intent=?", (values[0],)
                                ).fetchone()
                            ):
                                raise ArchiveBlocked("Invalid restored managed snapshot action")
                            ledger.db.execute(
                                "INSERT INTO managed_snapshot_action VALUES(?,?,?)", values
                            )
                        elif item["type"] == "managed_snapshot_scope" and has_pins:
                            values = item.get("values")
                            if (
                                type(values) is not list
                                or len(values) != 4
                                or values[0] != 1
                                or type(values[1]) is not str
                                or not re.fullmatch(r"[a-f0-9]{32}", values[1])
                                or type(values[2]) is not str
                                or not re.fullmatch(r"[a-f0-9]{64}", values[2])
                                or values[3] not in {"current", "needs_reconcile"}
                            ):
                                raise ArchiveBlocked("Invalid restored snapshot scope")
                            ledger.db.execute(
                                "INSERT INTO managed_snapshot_scope VALUES(?,?,?,'needs_reconcile')",
                                values[:3],
                            )
                        elif item["type"] == "managed_snapshot_epoch" and has_pins:
                            values = item.get("values")
                            if (
                                type(values) is not list
                                or len(values) != 2
                                or any(
                                    type(value) is not str
                                    or not re.fullmatch(r"[a-f0-9]{32}", value)
                                    for value in values
                                )
                                or not ledger.db.execute(
                                    "SELECT 1 FROM managed_snapshot WHERE intent=?", (values[0],)
                                ).fetchone()
                            ):
                                raise ArchiveBlocked(
                                    "Invalid restored managed snapshot epoch binding"
                                )
                            ledger.db.execute(
                                "INSERT INTO managed_snapshot_epoch VALUES(?,?)", values
                            )
                        elif item["type"] == "managed_snapshot_pin" and has_pins:
                            values = item.get("values")
                            if (
                                type(values) is not list
                                or len(values) != 10
                                or type(values[0]) is not str
                                or not re.fullmatch(r"[a-f0-9]{32}", values[0])
                                or type(values[1]) is not str
                                or not re.fullmatch(r"[a-f0-9]{32}", values[1])
                                or type(values[2]) is not int
                                or values[2] <= 0
                                or values[3]
                                not in {
                                    "restoreinput",
                                    "raw_pilot",
                                    "provider_reader",
                                    "root_check",
                                }
                                or type(values[4]) is not str
                                or not re.fullmatch(r"[a-f0-9]{64}", values[4])
                                or type(values[5]) is not str
                                or values[6] not in {"active", "uncertain", "released"}
                                or type(values[7]) is not str
                                or (values[8] is not None and type(values[8]) is not str)
                                or (
                                    values[9] is not None
                                    and (
                                        type(values[9]) is not str
                                        or not re.fullmatch(r"[a-f0-9]{64}", values[9])
                                    )
                                )
                                or (
                                    values[6] == "released"
                                    and (values[8] is None or values[9] is None)
                                )
                                or (
                                    values[6] in {"active", "uncertain"}
                                    and (values[8] is not None or values[9] is not None)
                                )
                            ):
                                raise ArchiveBlocked("Invalid restored snapshot pin")
                            try:
                                owner = json.loads(values[5])
                                if (
                                    type(owner) is not dict
                                    or set(owner)
                                    != {"host", "unit", "invocation", "pid_generation"}
                                    or any(
                                        type(value) is not str or not 1 <= len(value) <= 256
                                        for value in owner.values()
                                    )
                                ):
                                    raise ValueError
                            except (TypeError, ValueError):
                                raise ArchiveBlocked("Invalid restored pin owner") from None
                            ledger.db.execute(
                                "INSERT INTO managed_snapshot_pin VALUES(?,?,?,?,?,?,?,?,?,?)",
                                values,
                            )
                        elif item["type"] == "managed_snapshot_pin_event" and has_pins:
                            values = item.get("values")
                            if (
                                type(values) is not list
                                or len(values) != 5
                                or type(values[0]) is not int
                                or values[0] <= 0
                                or type(values[1]) is not str
                                or not re.fullmatch(r"[a-f0-9]{32}", values[1])
                                or values[2] not in {"active", "uncertain", "released"}
                                or type(values[3]) is not str
                                or type(values[4]) is not str
                                or not ledger.db.execute(
                                    "SELECT 1 FROM managed_snapshot_pin WHERE id=?", (values[1],)
                                ).fetchone()
                            ):
                                raise ArchiveBlocked("Invalid restored snapshot pin event")
                            ledger.db.execute(
                                "INSERT INTO managed_snapshot_pin_event VALUES(?,?,?,?,?)", values
                            )
                        elif item["type"] == "managed_snapshot_pin_receipt" and has_pins:
                            values = item.get("values")
                            if (
                                type(values) is not list
                                or len(values) != 3
                                or type(values[0]) is not str
                                or not re.fullmatch(r"[a-f0-9]{32}", values[0])
                                or type(values[1]) is not str
                                or type(values[2]) is not str
                                or not re.fullmatch(r"[a-f0-9]{64}", values[2])
                                or hashlib.sha256((values[1] + "\n").encode()).hexdigest()
                                != values[2]
                                or not ledger.db.execute(
                                    "SELECT 1 FROM managed_snapshot_pin WHERE id=?", (values[0],)
                                ).fetchone()
                            ):
                                raise ArchiveBlocked("Invalid restored snapshot pin receipt")
                            try:
                                receipt = json.loads(values[1])
                                pin = ledger.db.execute(
                                    "SELECT intent,snapshot_id,kind,operation_sha256,owner FROM managed_snapshot_pin WHERE id=?",
                                    (values[0],),
                                ).fetchone()
                                scope = ledger.db.execute(
                                    "SELECT epoch FROM managed_snapshot_scope WHERE id=1"
                                ).fetchone()
                                if (
                                    type(receipt) is not dict
                                    or receipt.get("schema") != 1
                                    or receipt.get("pin_id") != values[0]
                                    or receipt.get("state") != "active"
                                    or not pin
                                    or not scope
                                    or receipt.get("snapshot_intent") != pin[0]
                                    or receipt.get("snapshot_id") != pin[1]
                                    or receipt.get("kind") != pin[2]
                                    or receipt.get("operation_sha256") != pin[3]
                                    or receipt.get("owner") != json.loads(pin[4])
                                    or receipt.get("scope_epoch") != scope[0]
                                ):
                                    raise ValueError
                            except (TypeError, ValueError):
                                raise ArchiveBlocked(
                                    "Invalid restored pin receipt content"
                                ) from None
                            ledger.db.execute(
                                "INSERT INTO managed_snapshot_pin_receipt VALUES(?,?,?)", values
                            )
                        elif item["type"] in registry_v5.TABLES and index["schema"] == 5:
                            name = item["type"]
                            if (
                                set(item) != {"type", "values"}
                                or not extension["tables"][name]["present"]
                            ):
                                raise ArchiveBlocked("Unexpected V5 journal record")
                            values = item["values"]
                            registry_v5.validate(name, values, expected_environment, ledger)
                            if name == "sql_audit_v3_scope":
                                values[3] = "needs_reconcile"
                            ledger.db.execute(
                                "INSERT INTO "
                                + name
                                + " VALUES ("
                                + ",".join("?" for _ in values)
                                + ")",
                                values,
                            )
                            restored_v5[name] += 1
                            if restored_v5[name] > extension["tables"][name]["rows"]:
                                raise ArchiveBlocked("Excess V5 journal records")
                        else:
                            raise ArchiveBlocked("Unknown catalogue record")
                target.unlink()
            if index["schema"] == 5 and any(
                restored_v5[name] != extension["tables"][name]["rows"] for name in restored_v5
            ):
                raise ArchiveBlocked("Incomplete V5 journal coverage")
            if (
                has_pins
                and not ledger.db.execute(
                    "SELECT 1 FROM managed_snapshot_scope WHERE id=1"
                ).fetchone()
            ):
                raise ArchiveBlocked("Restored catalogue lacks snapshot pin scope")
            if has_pins:
                for pin_id, intent, snapshot_id, state, op_sha, completion in ledger.db.execute(
                    "SELECT id,intent,snapshot_id,state,operation_sha256,completion_sha256 FROM managed_snapshot_pin"
                ):
                    binding = ledger.db.execute(
                        "SELECT s.snapshot,e.epoch,c.epoch FROM managed_snapshot s JOIN managed_snapshot_epoch e ON e.intent=s.intent JOIN managed_snapshot_scope c ON c.id=1 WHERE s.intent=?",
                        (intent,),
                    ).fetchone()
                    if (
                        not binding
                        or binding[1] != binding[2]
                        or not binding[0]
                        or json.loads(binding[0]).get("id") != snapshot_id
                    ):
                        raise ArchiveBlocked("Restored snapshot pin ownership differs")
                    event = ledger.db.execute(
                        "SELECT state,evidence FROM managed_snapshot_pin_event WHERE pin_id=? ORDER BY ordinal DESC LIMIT 1",
                        (pin_id,),
                    ).fetchone()
                    if not event or event != (state, completion if state == "released" else op_sha):
                        raise ArchiveBlocked("Restored snapshot pin event differs")
    ledger.publish_read_view()
    return {"restored_bundles": count, "source_accesses": 0}
