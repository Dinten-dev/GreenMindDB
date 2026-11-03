"""Versioned bounded science/SQL journal catalogue records, no source row values."""

import base64
import hashlib
import json
import re

from app.raw_archive.policy import ArchiveBlocked

from .storage import validate_key

TABLES = {
    "science_v3_part": (
        ("token", "TEXT", 0, 1),
        ("binding", "TEXT", 1, 0),
        ("state", "TEXT", 1, 0),
        ("reference", "TEXT", 1, 0),
    ),
    "science_v3_whole": (
        ("token", "TEXT", 0, 1),
        ("body_sha256", "TEXT", 1, 0),
        ("state", "TEXT", 1, 0),
        ("reference", "TEXT", 1, 0),
    ),
    "science_v3_restore": (
        ("whole_sha256", "TEXT", 1, 1),
        ("position", "INTEGER", 1, 2),
        ("audit", "TEXT", 1, 0),
    ),
    "sql_deletion_pilot": (
        ("pilot_id", "TEXT", 0, 1),
        ("candidate", "TEXT", 1, 0),
        ("authorization_sha256", "TEXT", 1, 0),
    ),
    "sql_deletion_epoch": (
        ("id", "INTEGER", 0, 1),
        ("epoch", "TEXT", 1, 0),
        ("genesis", "TEXT", 1, 0),
    ),
    "science_v3_scope_epoch": (
        ("id", "INTEGER", 0, 1),
        ("epoch", "TEXT", 1, 0),
        ("source_session", "TEXT", 1, 0),
        ("capture_id", "TEXT", 1, 0),
        ("capture_sha256", "TEXT", 1, 0),
        ("index_sha256", "TEXT", 1, 0),
        ("frame_sha256", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
    ),
    "science_v3_scope_history": (
        ("epoch", "TEXT", 0, 1),
        ("previous_epoch", "TEXT", 1, 0),
        ("inventory_sha256", "TEXT", 1, 0),
        ("authority_session", "TEXT", 1, 0),
        ("created_at", "TEXT", 1, 0),
    ),
    "sql_audit_v3_backup": (
        ("epoch", "TEXT", 0, 1),
        ("candidate", "TEXT", 1, 0),
        ("gateway_manifest_sha256", "TEXT", 1, 0),
        ("audit_sha256", "TEXT", 1, 0),
        ("plaintext_sha256", "TEXT", 1, 0),
    ),
    "sql_audit_v3_backup_history": (
        ("epoch", "TEXT", 1, 1),
        ("candidate", "TEXT", 1, 2),
        ("gateway_manifest_sha256", "TEXT", 1, 0),
        ("audit_sha256", "TEXT", 1, 0),
        ("plaintext_sha256", "TEXT", 1, 0),
    ),
    "sql_audit_v3_scope": (
        ("id", "INTEGER", 0, 1),
        ("epoch", "TEXT", 1, 0),
        ("genesis_sha256", "TEXT", 1, 0),
        ("state", "TEXT", 1, 0),
    ),
    "sql_audit_v3_scope_proof": (
        ("id", "INTEGER", 0, 1),
        ("epoch", "TEXT", 1, 0),
        ("body", "TEXT", 1, 0),
        ("body_sha256", "TEXT", 1, 0),
    ),
    "science_v3_import_provenance": (
        ("id", "TEXT", 0, 1),
        ("manifest_sha256", "TEXT", 1, 0),
        ("inventory_sha256", "TEXT", 1, 0),
        ("record", "TEXT", 1, 0),
    ),
}
DDL = {
    "science_v3_part": "CREATE TABLE IF NOT EXISTS science_v3_part (token TEXT PRIMARY KEY, binding TEXT NOT NULL, state TEXT NOT NULL, reference TEXT NOT NULL)",
    "science_v3_whole": "CREATE TABLE IF NOT EXISTS science_v3_whole (token TEXT PRIMARY KEY, body_sha256 TEXT NOT NULL, state TEXT NOT NULL, reference TEXT NOT NULL)",
    "science_v3_restore": "CREATE TABLE IF NOT EXISTS science_v3_restore (whole_sha256 TEXT NOT NULL, position INTEGER NOT NULL, audit TEXT NOT NULL, PRIMARY KEY(whole_sha256,position))",
    "sql_deletion_pilot": "CREATE TABLE IF NOT EXISTS sql_deletion_pilot (pilot_id TEXT PRIMARY KEY, candidate TEXT NOT NULL, authorization_sha256 TEXT NOT NULL)",
    "sql_deletion_epoch": "CREATE TABLE IF NOT EXISTS sql_deletion_epoch (id INTEGER PRIMARY KEY CHECK(id=1), epoch TEXT NOT NULL, genesis TEXT NOT NULL)",
    "science_v3_scope_epoch": "CREATE TABLE IF NOT EXISTS science_v3_scope_epoch (id INTEGER PRIMARY KEY CHECK(id=1), epoch TEXT NOT NULL, source_session TEXT NOT NULL, capture_id TEXT NOT NULL, capture_sha256 TEXT NOT NULL, index_sha256 TEXT NOT NULL, frame_sha256 TEXT NOT NULL, created_at TEXT NOT NULL)",
    "science_v3_scope_history": "CREATE TABLE IF NOT EXISTS science_v3_scope_history (epoch TEXT PRIMARY KEY, previous_epoch TEXT NOT NULL, inventory_sha256 TEXT NOT NULL, authority_session TEXT NOT NULL, created_at TEXT NOT NULL)",
    "sql_audit_v3_backup": "CREATE TABLE IF NOT EXISTS sql_audit_v3_backup (epoch TEXT PRIMARY KEY, candidate TEXT NOT NULL, gateway_manifest_sha256 TEXT NOT NULL, audit_sha256 TEXT NOT NULL, plaintext_sha256 TEXT NOT NULL)",
    "sql_audit_v3_backup_history": "CREATE TABLE IF NOT EXISTS sql_audit_v3_backup_history (epoch TEXT NOT NULL, candidate TEXT NOT NULL, gateway_manifest_sha256 TEXT NOT NULL, audit_sha256 TEXT NOT NULL, plaintext_sha256 TEXT NOT NULL, PRIMARY KEY(epoch,candidate))",
    "sql_audit_v3_scope": "CREATE TABLE IF NOT EXISTS sql_audit_v3_scope (id INTEGER PRIMARY KEY CHECK(id=1), epoch TEXT NOT NULL, genesis_sha256 TEXT NOT NULL, state TEXT NOT NULL)",
    "sql_audit_v3_scope_proof": "CREATE TABLE IF NOT EXISTS sql_audit_v3_scope_proof (id INTEGER PRIMARY KEY CHECK(id=1), epoch TEXT NOT NULL, body TEXT NOT NULL, body_sha256 TEXT NOT NULL)",
    "science_v3_import_provenance": "CREATE TABLE IF NOT EXISTS science_v3_import_provenance (id TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL, inventory_sha256 TEXT NOT NULL, record TEXT NOT NULL)",
}


def need(ok, message):
    if not ok:
        raise ArchiveBlocked(message)


def sha(value, length=64):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{" + str(length) + "}", value) is not None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json(raw):
    need(type(raw) is str and len(raw.encode()) <= 1024 * 1024, "Bounded V5 journal JSON required")
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ArchiveBlocked("Invalid V5 journal JSON") from exc
    need(type(obj) is dict and canonical(obj) == raw, "Canonical V5 journal object required")
    return obj


def reference(raw, expected_plain, environment):
    item = _json(raw)
    need(
        set(item) == {"format", "sha256", "bytes", "plaintext_sha256", "key"}
        and item["format"] == "bin"
        and sha(item["sha256"])
        and item["plaintext_sha256"] == expected_plain
        and type(item["bytes"]) is int
        and 0 < item["bytes"] <= 64 * 1024**2
        and type(item["key"]) is str
        and item["key"].startswith(environment + "/")
        and item["key"].endswith("/" + item["sha256"] + ".bin"),
        "Invalid V5 encrypted reference",
    )
    validate_key(item["key"])
    return item


def validate(kind, values, environment, ledger):
    need(
        kind in TABLES and type(values) is list and len(values) == len(TABLES[kind]),
        "Unknown V5 journal record",
    )
    if kind == "sql_audit_v3_scope_proof":
        item_id, epoch, raw, body_sha = values
        body = _json(raw)
        fields = {
            "format",
            "epoch",
            "genesis_sha256",
            "source_identity_sha256",
            "audit_schema_sha256",
            "backup_candidate",
            "backup_manifest_sha256",
            "audit_plaintext_sha256",
            "snapshot_sha256",
            "restore_evidence_sha256",
            "registry_reference_sha256",
            "history_sha256",
            "inventory_sha256",
            "runtime_sha256",
            "bindings_sha256",
            "approvals",
            "commits",
        }
        need(
            item_id == 1
            and sha(epoch, 32)
            and sha(body_sha)
            and hashlib.sha256(raw.encode()).hexdigest() == body_sha
            and set(body) == fields
            and body["format"] == "greenmind.sql-audit-v3-scope-proof.v1"
            and body["epoch"] == epoch
            and all(sha(body[k]) for k in fields - {"format", "epoch", "approvals", "commits"})
            and type(body["approvals"]) is int
            and body["approvals"] in (0, 1)
            and type(body["commits"]) is int
            and body["commits"] == body["approvals"],
            "Invalid V3 SQL scope proof",
        )
        scope = ledger.db.execute(
            "SELECT epoch,genesis_sha256,state FROM sql_audit_v3_scope WHERE id=1"
        ).fetchone()
        history_exists = ledger.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sql_audit_v3_backup_history'"
        ).fetchone()
        anchor = (
            ledger.db.execute(
                "SELECT candidate,gateway_manifest_sha256,plaintext_sha256 FROM sql_audit_v3_backup_history WHERE epoch=? AND candidate=?",
                (epoch, body["backup_candidate"]),
            ).fetchone()
            if history_exists
            else None
        )
        if anchor is None:
            anchor = ledger.db.execute(
                "SELECT candidate,gateway_manifest_sha256,plaintext_sha256 FROM sql_audit_v3_backup WHERE epoch=? AND candidate=?",
                (epoch, body["backup_candidate"]),
            ).fetchone()
        need(
            scope is not None
            and tuple(scope[:2]) == (epoch, body["genesis_sha256"])
            and scope[2] in {"current", "needs_reconcile"}
            and anchor is not None
            and tuple(anchor)
            == (
                body["backup_candidate"],
                body["backup_manifest_sha256"],
                body["audit_plaintext_sha256"],
            ),
            "V3 SQL scope proof anchor differs",
        )
        receipt = ledger.get(body["backup_candidate"])
        need(
            type(receipt) is dict
            and type(receipt.get("files")) is list
            and len(receipt["files"]) == 6
            and type(receipt["files"][0]) is dict
            and receipt["files"][0].get("audit_v3_schema_sha256") == body["audit_schema_sha256"]
            and receipt["files"][0].get("audit_v3_snapshot_sha256") == body["snapshot_sha256"],
            "V3 SQL scope snapshot differs",
        )
    elif kind == "sql_audit_v3_scope":
        item_id, epoch, genesis_sha, state = values
        need(
            item_id == 1
            and sha(epoch, 32)
            and sha(genesis_sha)
            and state in {"current", "needs_reconcile"},
            "Invalid V3 SQL audit scope",
        )
        need(
            ledger.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sql_audit_v3_backup'"
            ).fetchone()
            is not None,
            "V3 SQL audit backup inventory absent",
        )
        anchor = ledger.db.execute(
            "SELECT candidate FROM sql_audit_v3_backup WHERE epoch=?", (epoch,)
        ).fetchone()
        need(anchor is not None, "V3 SQL audit scope backup anchor absent")
        receipt = ledger.get(anchor[0])
        need(
            type(receipt) is dict
            and type(receipt.get("files")) is list
            and len(receipt["files"]) == 6
            and type(receipt["files"][0]) is dict
            and receipt["files"][0].get("audit_v3_genesis_sha256") == genesis_sha,
            "V3 SQL audit scope genesis differs",
        )
    elif kind in {"sql_audit_v3_backup", "sql_audit_v3_backup_history"}:
        epoch, candidate, manifest_sha, audit_sha, plain_sha = values
        receipt = ledger.get(candidate) if sha(candidate) else None
        need(
            sha(epoch, 32)
            and all(sha(value) for value in (candidate, manifest_sha, audit_sha, plain_sha))
            and type(receipt) is dict
            and receipt.get("state") in {"published", "verified"}
            and receipt.get("source", {}).get("source") == "gateway"
            and receipt.get("source", {}).get("dataset") == "database_backup"
            and receipt.get("manifest", {}).get("sha256") == manifest_sha
            and type(receipt.get("files")) is list
            and len(receipt["files"]) == 6
            and receipt["files"][0].get("backup_format") == "schema-master-provider-sql-audit-v3"
            and receipt["files"][0].get("audit_v3_plaintext_sha256") == plain_sha
            and receipt["files"][0].get("audit_v3_epoch") == epoch
            and receipt["files"][5].get("sha256") == audit_sha
            and receipt["files"][5].get("plaintext_sha256") == plain_sha,
            "Invalid V3 SQL audit backup receipt binding",
        )
    elif kind == "sql_deletion_epoch":
        item_id, epoch, raw = values
        genesis = _json(raw)
        need(
            item_id == 1
            and sha(epoch, 32)
            and set(genesis)
            == {
                "format",
                "epoch",
                "source_identity_sha256",
                "audit_schema_sha256",
                "origin_inventory_sha256",
                "registry_reference_sha256",
                "runtime_sha256",
                "bindings_sha256",
                "proof_sha256",
            }
            and genesis["format"] == "greenmind.sql-scope-genesis.v1"
            and genesis["epoch"] == epoch
            and all(
                sha(genesis[k])
                for k in (
                    "source_identity_sha256",
                    "audit_schema_sha256",
                    "origin_inventory_sha256",
                    "registry_reference_sha256",
                    "runtime_sha256",
                    "bindings_sha256",
                    "proof_sha256",
                )
            ),
            "Invalid SQL scope genesis",
        )
    elif kind == "sql_deletion_pilot":
        pilot, candidate, auth = values
        receipt = ledger.get(candidate) if sha(candidate) else None
        need(
            sha(pilot, 32)
            and sha(candidate)
            and sha(auth)
            and receipt is not None
            and receipt["source"].get("kind") in {"features", "dashboard", "logs"}
            and receipt["source"].get("dataset")
            in {
                "ingest_log",
                "gateway_state_report",
                "visual_reading",
                "visual_wave",
                "wav_feature",
                "wav_feature_version",
                "direct_visual_point",
            },
            "Invalid V5 SQL pilot budget binding",
        )
    elif kind == "science_v3_part":
        token, binding_raw, state, ref_raw = values
        binding = _json(binding_raw)
        keys = {
            "capture_sha256",
            "index_sha256",
            "capture_id",
            "position",
            "part",
            "destination",
            "key_prefix",
        }
        need(
            sha(token)
            and hashlib.sha256(binding_raw.encode()).hexdigest() == token
            and set(binding) == keys
            and sha(binding["capture_sha256"])
            and sha(binding["index_sha256"])
            and sha(binding["capture_id"], 32)
            and type(binding["position"]) is int
            and binding["position"] >= 0
            and type(binding["destination"]) is str
            and re.fullmatch(
                r"sftp://[A-Za-z0-9_-]+@[A-Za-z0-9.-]+:[0-9]{1,5}/[A-Za-z0-9_-]+",
                binding["destination"],
            )
            and type(binding["key_prefix"]) is str
            and state in {"prepared", "verified"},
            "Invalid V5 science part binding",
        )
        part = binding["part"]
        need(
            type(part) is dict
            and set(part)
            == {"table", "sequence", "first_row", "rows", "start", "end", "bytes", "sha256"}
            and part["table"] in {"wav_feature", "wav_feature_version"}
            and all(
                type(part[key]) is int and part[key] >= 0
                for key in ("sequence", "first_row", "rows", "start", "end", "bytes")
            )
            and part["rows"] <= 20000
            and 21 <= part["bytes"] <= 32 * 1024**2
            and part["end"] >= part["start"]
            and part["bytes"] == 21 + part["end"] - part["start"]
            and sha(part["sha256"]),
            "Invalid V5 science part range",
        )
        ref = reference(ref_raw, part["sha256"], environment)
        need(
            ref["key"] == binding["key_prefix"] + ref["sha256"] + ".bin",
            "Science key prefix differs",
        )
    elif kind == "science_v3_whole":
        token, body, state, ref = values
        need(
            sha(token)
            and sha(body)
            and token == hashlib.sha256(("whole:" + body).encode()).hexdigest()
            and state in {"prepared", "verified"},
            "Invalid V5 whole binding",
        )
        reference(ref, body, environment)
    elif kind == "science_v3_scope_history":
        from .policy import utc

        epoch, previous, inventory, session, created_at = values
        need(
            sha(epoch, 32)
            and sha(previous, 32)
            and epoch != previous
            and sha(inventory)
            and sha(session, 32)
            and type(created_at) is str,
            "Invalid V5 science scope history",
        )
        utc(created_at)
    elif kind == "science_v3_scope_epoch":
        from .policy import utc

        identity, epoch, session, capture_id, capture_sha, index_sha, frame_sha, created_at = values
        need(
            type(identity) is int
            and identity == 1
            and sha(epoch, 32)
            and sha(session, 32)
            and sha(capture_id, 32)
            and sha(capture_sha)
            and sha(index_sha)
            and sha(frame_sha)
            and type(created_at) is str,
            "Invalid V5 science scope epoch",
        )
        utc(created_at)
        if ledger.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='science_v3_part'"
        ).fetchone():
            for (raw_binding,) in ledger.db.execute("SELECT binding FROM science_v3_part"):
                binding = _json(raw_binding)
                need(
                    binding.get("capture_id") == capture_id
                    and binding.get("capture_sha256") == capture_sha
                    and binding.get("index_sha256") == index_sha,
                    "V5 science epoch differs from archived parts",
                )
    elif kind == "science_v3_import_provenance":
        identity, manifest_sha, inventory_sha, raw = values
        record = _json(raw)
        fields = {
            "format",
            "manifest_sha256",
            "inventory_sha256",
            "source_ledger_identity_sha256",
            "authority_ledger_identity_sha256",
            "source_machine_sha256",
            "source_binding_sha256",
            "provider_evidence_sha256",
            "provider_terminal_sha256",
            "bootstrap_receipt_sha256",
            "encryption_key_sha256",
            "source_database_sha256",
            "source_wal_sha256",
            "observation_session",
            "scope_epoch",
            "scope_source_session",
            "scope_frame_sha256",
            "capture_id",
            "capture_sha256",
            "index_sha256",
            "whole_sha256",
            "destination_sha256",
            "part_count",
            "audit_count",
            "evidence",
        }
        need(
            set(record) == fields
            and record["format"] == "greenmind.science-registry-import.v1"
            and sha(identity)
            and sha(manifest_sha)
            and sha(inventory_sha)
            and record["manifest_sha256"] == manifest_sha
            and record["inventory_sha256"] == inventory_sha
            and all(
                sha(record[k])
                for k in (
                    "source_ledger_identity_sha256",
                    "authority_ledger_identity_sha256",
                    "source_machine_sha256",
                    "source_binding_sha256",
                    "source_database_sha256",
                    "scope_frame_sha256",
                    "provider_evidence_sha256",
                    "provider_terminal_sha256",
                    "bootstrap_receipt_sha256",
                    "encryption_key_sha256",
                    "capture_sha256",
                    "index_sha256",
                    "whole_sha256",
                    "destination_sha256",
                )
            )
            and (record["source_wal_sha256"] is None or sha(record["source_wal_sha256"]))
            and all(
                sha(record[k], 32)
                for k in (
                    "observation_session",
                    "scope_epoch",
                    "scope_source_session",
                    "capture_id",
                )
            )
            and type(record["part_count"]) is int
            and type(record["audit_count"]) is int
            and record["part_count"] == record["audit_count"] == 46,
            "Invalid V5 science import provenance",
        )
        evidence = record["evidence"]
        try:
            need(
                type(evidence) is dict
                and set(evidence)
                == {"format", "sha256", "plaintext_sha256", "bytes", "ciphertext_base64"}
                and evidence["format"] == "aes-256-gcm-science-evidence.v1"
                and sha(evidence["sha256"])
                and sha(evidence["plaintext_sha256"])
                and type(evidence["bytes"]) is int
                and 28 <= evidence["bytes"] <= 384 * 1024 + 28
                and type(evidence["ciphertext_base64"]) is str
                and len(evidence["ciphertext_base64"]) <= 524400,
                "Invalid V5 science evidence",
            )
            ciphertext = base64.b64decode(evidence["ciphertext_base64"], validate=True)
            need(
                len(ciphertext) == evidence["bytes"]
                and hashlib.sha256(ciphertext).hexdigest() == evidence["sha256"],
                "V5 science evidence ciphertext differs",
            )
        except ArchiveBlocked:
            raise
        except Exception:
            raise ArchiveBlocked("Invalid V5 science evidence") from None
        expected = hashlib.sha256(
            (
                "science-v3-import:"
                + record["scope_epoch"]
                + ":"
                + record["capture_sha256"]
                + ":"
                + record["index_sha256"]
                + ":"
                + record["whole_sha256"]
            ).encode()
        ).hexdigest()
        epoch = ledger.db.execute(
            "SELECT epoch,source_session,capture_id,capture_sha256,index_sha256,frame_sha256 FROM science_v3_scope_epoch WHERE id=1"
        ).fetchone()
        need(
            identity == expected
            and epoch
            == (
                record["scope_epoch"],
                record["scope_source_session"],
                record["capture_id"],
                record["capture_sha256"],
                record["index_sha256"],
                record["scope_frame_sha256"],
            )
            and ledger.db.execute(
                "SELECT count(*) FROM science_v3_part WHERE state='verified'"
            ).fetchone()[0]
            == 46
            and ledger.db.execute("SELECT count(*) FROM science_v3_part").fetchone()[0] == 46
            and ledger.db.execute(
                "SELECT count(*) FROM science_v3_whole WHERE state='verified' AND body_sha256=?",
                (record["whole_sha256"],),
            ).fetchone()[0]
            == 1
            and ledger.db.execute("SELECT count(*) FROM science_v3_whole").fetchone()[0] == 1
            and ledger.db.execute(
                "SELECT count(*) FROM science_v3_restore WHERE whole_sha256=?",
                (record["whole_sha256"],),
            ).fetchone()[0]
            == 46
            and ledger.db.execute("SELECT count(*) FROM science_v3_restore").fetchone()[0] == 46,
            "Science import provenance lacks complete matching journal",
        )
        positions = set()
        for (raw_binding,) in ledger.db.execute("SELECT binding FROM science_v3_part"):
            binding = _json(raw_binding)
            need(
                hashlib.sha256(binding["destination"].encode()).hexdigest()
                == record["destination_sha256"],
                "Science import provenance destination differs",
            )
            positions.add(binding["position"])
        need(positions == set(range(46)), "Science import part positions differ")
        tables = (
            "science_v3_part",
            "science_v3_whole",
            "science_v3_restore",
            "science_v3_scope_epoch",
            "science_v3_scope_history",
        )
        need(
            ledger.db.execute("SELECT count(*) FROM science_v3_scope_history").fetchone()[0] <= 64,
            "Science import history exceeds budget",
        )
        total_bytes = 0
        for table in tables:
            expression = "+".join(
                'coalesce(length(CAST("' + column[0] + '" AS BLOB)),0)' for column in TABLES[table]
            )
            total_bytes += ledger.db.execute(
                "SELECT coalesce(sum(" + expression + "),0) FROM " + table
            ).fetchone()[0]
            need(total_bytes <= 4 * 1024**2, "Science import inventory exceeds budget")
        frozen = {
            table: [
                list(row) for row in ledger.db.execute("SELECT * FROM " + table + " ORDER BY 1,2")
            ]
            for table in tables
        }
        need(
            hashlib.sha256(canonical(frozen).encode()).hexdigest() == inventory_sha,
            "Science import frozen inventory differs",
        )
    else:
        whole, position, raw = values
        audit = _json(raw)
        need(
            sha(whole)
            and type(position) is int
            and position >= 0
            and set(audit)
            == {
                "format",
                "whole_sha256",
                "capture_id",
                "index_sha256",
                "position",
                "part_sha256",
                "rows",
                "target",
                "native_row_multiset_equal",
                "parent_fk_closure_proven",
                "writer_allowed",
            }
            and audit["format"] == "greenmind.science-part-restore.v3"
            and audit["whole_sha256"] == whole
            and audit["position"] == position
            and sha(audit["capture_id"], 32)
            and sha(audit["index_sha256"])
            and sha(audit["part_sha256"])
            and type(audit["rows"]) is int
            and 0 <= audit["rows"] <= 20000
            and type(audit["target"]) is dict
            and audit["native_row_multiset_equal"] is True
            and audit["parent_fk_closure_proven"] is False
            and audit["writer_allowed"] is False,
            "Invalid V5 native restore audit",
        )
        need(
            ledger.db.execute(
                "SELECT 1 FROM science_v3_whole WHERE body_sha256=? AND state='verified'", (whole,)
            ).fetchone()
            is not None,
            "Native restore audit lacks verified whole",
        )
        target = audit["target"]
        need(
            set(target)
            == {
                "database",
                "database_oid",
                "role",
                "role_oid",
                "port",
                "server_address",
                "postmaster_start",
            }
            and type(target["database"]) is str
            and re.fullmatch(r"science_v3_[a-f0-9]{32}", target["database"])
            and target["role"] == target["database"]
            and all(
                type(target[key]) is int and target[key] > 0
                for key in ("database_oid", "role_oid", "port")
            )
            and 1024 <= target["port"] <= 65535
            and target["port"] not in (5432, 5433)
            and type(target["server_address"]) is str
            and len(target["server_address"]) <= 64
            and type(target["postmaster_start"]) is str
            and len(target["postmaster_start"]) <= 64,
            "Invalid V5 native restore target binding",
        )
        need(
            ledger.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='science_v3_part'"
            ).fetchone(),
            "Native restore audit lacks part journal",
        )
        matching = ledger.db.execute(
            "SELECT binding FROM science_v3_part WHERE state='verified' AND json_extract(binding,'$.capture_id')=? AND json_extract(binding,'$.position')=?",
            (audit["capture_id"], position),
        ).fetchall()
        need(len(matching) == 1, "Native restore audit lacks one verified part")
        bound = _json(matching[0][0])
        need(
            bound["index_sha256"] == audit["index_sha256"]
            and bound["part"]["sha256"] == audit["part_sha256"]
            and bound["part"]["rows"] == audit["rows"],
            "Native restore audit differs from verified part",
        )


def schema(db, name):
    need(name in TABLES, "Unknown V5 table")
    rows = db.execute("PRAGMA table_info(" + name + ")").fetchall()
    need(
        tuple((row[1], row[2].upper(), row[3], row[5]) for row in rows) == TABLES[name],
        "V5 journal schema differs",
    )
