"""Bounded V3 Source-audit snapshot captured inside Gateway's RR transaction.

No Source write, catalogue publication or production acceptance is performed.
The caller must bind this artifact to a versioned Gateway backup receipt.
"""

import hashlib
import json
import os
import stat
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from app.raw_archive.policy import ArchiveBlocked, checksum

from . import backup, sql_audit_v3

FORMAT = "greenmind.sql-source-audit-snapshot.v3"
TABLES = {
    "generation": ("gm_sql_v3_generation", "singleton", 2),
    "approvals": ("gm_sql_v3_approval", "slot", 129),
    "events": ("gm_sql_v3_event", "slot", 129),
    "rows": ("gm_sql_v3_row", "slot,hash", 4097),
    "unknown": ("gm_sql_v3_unknown", "singleton", 2),
}


def need(condition, message):
    if not condition:
        raise ArchiveBlocked(message)



def encode(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ArchiveBlocked("V3 audit snapshot encoding differs") from exc
    need(len(raw) <= sql_audit_v3.MAX_SNAPSHOT_BYTES, "V3 audit snapshot exceeds 1 MiB")
    return raw


def decode(raw):
    need(type(raw) is bytes and len(raw) <= sql_audit_v3.MAX_SNAPSHOT_BYTES, "V3 audit snapshot size differs")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ArchiveBlocked("V3 audit snapshot encoding differs") from exc
    need(encode(result) == raw, "V3 audit snapshot is not canonical")
    return result


def capture_structure(db, writer_role):
    """Capture native, OID-independent audit schema and effective writer grants."""
    need(db.dialect.name == "postgresql" and type(writer_role) is str and sql_audit_v3.NAME.fullmatch(writer_role), "Exact native audit writer required")
    identity = db.execute(text("SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=:role"), {"role": writer_role}).scalar_one()
    sql_audit_v3.inspect_writer(db, writer_role, identity)
    tables = {}
    for label, (table, _order, _limit) in TABLES.items():
        name = "public." + table
        columns = db.execute(text("""SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull
            FROM pg_catalog.pg_attribute a WHERE a.attrelid=pg_catalog.to_regclass(:name)
            AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum"""), {"name": name}).all()
        constraints = db.execute(text("""SELECT c.conname,c.contype,pg_catalog.pg_get_constraintdef(c.oid),
            c.condeferrable,c.condeferred,c.convalidated FROM pg_catalog.pg_constraint c
            WHERE c.conrelid=pg_catalog.to_regclass(:name) ORDER BY c.conname"""), {"name": name}).all()
        triggers = db.execute(text("""SELECT t.tgname,t.tgtype,t.tgenabled,t.tgnargs,p.proname,
            pg_catalog.pg_get_functiondef(p.oid) FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
            WHERE t.tgrelid=pg_catalog.to_regclass(:name) AND NOT t.tgisinternal ORDER BY t.tgname"""), {"name": name}).all()
        grants = db.execute(text("""SELECT pg_catalog.has_table_privilege(:role,:name,'SELECT'),
            pg_catalog.has_table_privilege(:role,:name,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER'),
            pg_catalog.has_table_privilege('public',:name,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER')"""), {"role": writer_role, "name": name}).one()
        need(bool(columns) and tuple(grants) == (True, False, False), "V3 native schema or grants differ")
        tables[label] = {"columns": [list(x) for x in columns], "constraints": [list(x) for x in constraints], "triggers": [list(x) for x in triggers], "grants": list(grants)}
    functions = db.execute(text("""SELECT p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),
        pg_catalog.pg_get_functiondef(p.oid),p.prosecdef,p.proconfig,
        pg_catalog.has_function_privilege(:role,p.oid,'EXECUTE'),
        pg_catalog.has_function_privilege('public',p.oid,'EXECUTE')
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN ('gm_sql_v3_arm','gm_sql_v3_capture',
        'gm_sql_v3_complete','gm_sql_v3_immutable','gm_sql_v3_validate_approval') ORDER BY p.proname"""), {"role": writer_role}).all()
    need(len(functions) == 5, "V3 native function roster differs")
    ingest = db.execute(text("""SELECT t.tgname,t.tgtype,t.tgenabled,t.tgnargs,p.proname,
        pg_catalog.pg_get_functiondef(p.oid) FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgrelid=pg_catalog.to_regclass('public.ingest_log') AND NOT t.tgisinternal ORDER BY t.tgname""")).all()
    need(any(row[0] == "gm_v3_capture" for row in ingest), "V3 Source capture trigger missing")
    result = {"format": "greenmind.sql-audit-v3-structure.v1", "tables": tables,
              "functions": [list(x) for x in functions], "ingest_triggers": [list(x) for x in ingest]}
    need(len(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()) <= 65536, "V3 native schema proof exceeds budget")
    return result

def validate(payload):
    """Validate complete immutable inventory; unknown latch is preserved, not approved."""
    need(type(payload) is dict and set(payload) == {"format", "source_identity", "schema_sha256", "snapshot_sha256", "data", "structure"} and payload["format"] == FORMAT, "V3 audit snapshot format differs")
    need(type(payload["source_identity"]) is dict and all(type(payload[k]) is str and sql_audit_v3.HEX64.fullmatch(payload[k]) for k in ("schema_sha256", "snapshot_sha256")), "V3 audit snapshot pins absent")
    structure = payload["structure"]
    need(type(structure) is dict and set(structure) == {"format", "tables", "functions", "ingest_triggers"}
         and structure["format"] == "greenmind.sql-audit-v3-structure.v1"
         and type(structure["tables"]) is dict and set(structure["tables"]) == set(TABLES)
         and type(structure["functions"]) is list and len(structure["functions"]) == 5
         and type(structure["ingest_triggers"]) is list and len(structure["ingest_triggers"]) > 0,
         "V3 native schema proof absent")
    need(all(type(item) is dict and set(item) == {"columns", "constraints", "triggers", "grants"}
             and all(type(item[k]) is list for k in item) and item["grants"] == [True, False, False]
             for item in structure["tables"].values()), "V3 native table structure differs")
    need(len(json.dumps(structure, sort_keys=True, separators=(",", ":")).encode()) <= 65536,
         "V3 native schema proof exceeds budget")
    data = payload["data"]
    need(type(data) is dict and set(data) == set(TABLES), "V3 audit table inventory differs")
    need(all(type(data[k]) is list and len(data[k]) < spec[2] for k, spec in TABLES.items()), "V3 audit inventory exceeds bounds")
    need(len(data["generation"]) == 1 and len(data["unknown"]) <= 1, "V3 audit generation differs")
    generation = data["generation"][0]
    need(type(generation) is dict and set(generation) == {"singleton", "epoch", "genesis_sha", "writer_oid"} and generation["singleton"] is True and type(generation["epoch"]) is str and sql_audit_v3.HEX32.fullmatch(generation["epoch"]) and type(generation["genesis_sha"]) is str and sql_audit_v3.HEX64.fullmatch(generation["genesis_sha"]) and type(generation["writer_oid"]) is int and generation["writer_oid"] > 0, "V3 generation identity differs")
    approvals = {}
    for a in data["approvals"]:
        need(type(a) is dict and set(a) == {"slot", "epoch", "writer_oid", "candidate_sha", "tenant_sha", "pk_sha", "permit_sha", "body", "expected", "start_at", "expires_at"}, "V3 approval fields differ")
        slot = a["slot"]
        need(type(slot) is int and 1 <= slot <= 128 and slot not in approvals and a["epoch"] == generation["epoch"] and a["writer_oid"] == generation["writer_oid"], "V3 approval identity differs")
        need(all(type(a[k]) is str and sql_audit_v3.HEX64.fullmatch(a[k]) for k in ("candidate_sha", "tenant_sha", "pk_sha", "permit_sha")) and type(a["body"]) is str and len(a["body"].encode()) <= 1024 and all(ord(c) >= 32 for c in a["body"]), "V3 approval scope differs")
        expected = a["expected"]
        need(type(expected) is list and 1 <= len(expected) <= 32 and all(type(h) is str and sql_audit_v3.HEX64.fullmatch(h) for h in expected) and len(set(expected)) == len(expected), "V3 approval rows differ")
        try:
            start, expiry = (datetime.fromisoformat(a[k]) for k in ("start_at", "expires_at"))
            valid_time = start.utcoffset() is not None and expiry.utcoffset() is not None and 0 < (expiry - start).total_seconds() <= 110
        except (TypeError, ValueError):
            valid_time = False
        need(valid_time, "V3 approval TTL differs")
        approvals[slot] = a
    events = {}
    for event in data["events"]:
        need(type(event) is dict and set(event) == {"slot", "txid", "created_at"} and type(event["slot"]) is int and event["slot"] in approvals and event["slot"] not in events and type(event["txid"]) is int and event["txid"] > 0, "V3 event differs")
        events[event["slot"]] = event
    need(len({e["txid"] for e in events.values()}) == len(events), "V3 event transaction repeated")
    rows = {}
    for row in data["rows"]:
        need(type(row) is dict and set(row) == {"slot", "hash"} and type(row["slot"]) is int and row["slot"] in events and type(row["hash"]) is str and sql_audit_v3.HEX64.fullmatch(row["hash"]), "V3 row differs")
        rows.setdefault(row["slot"], []).append(row["hash"])
    need(all(sorted(rows.get(slot, [])) == sorted(approvals[slot]["expected"]) for slot in events), "V3 operation incomplete")
    if data["unknown"]:
        latch = data["unknown"][0]
        need(type(latch) is dict and set(latch) == {"singleton", "txid", "created_at"} and latch["singleton"] is True and type(latch["txid"]) is int and latch["txid"] > 0, "V3 unknown latch differs")
    raw = encode(payload)
    need(len(raw) <= sql_audit_v3.MAX_SNAPSHOT_BYTES, "V3 audit snapshot exceeds 1 MiB")
    return payload


def history_binding(payload):
    """Normalize the actual encrypted snapshot to the live audit-history query."""
    from . import codec

    validate(payload)
    need(not payload["data"]["unknown"], "Unknown Source DELETE holds V3 history")
    data = payload["data"]
    approvals = [[a[k] for k in ("slot", "epoch", "candidate_sha", "permit_sha", "body", "expected")]
                 for a in data["approvals"]]
    events = [[item["slot"], str(item["txid"])] for item in data["events"]]
    rows = [[item["slot"], item["hash"]] for item in data["rows"]]
    return {"approvals": len(approvals), "commits": len(events),
            "history_sha256": codec.digest({"approvals": approvals, "events": events, "rows": rows})}


def snapshot_payload(db, snapshot, *, source_identity, schema_sha256, epoch, genesis_sha256, checkpoint):
    """Read complete audit data on a transaction already bound to the RR snapshot."""
    from .sql_deletion_guard import _identity

    need(db.dialect.name == "postgresql" and type(snapshot) is str and 0 < len(snapshot) <= 256, "Live PostgreSQL snapshot required")
    db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    need(db.execute(text("SHOW transaction_isolation")).scalar_one() == "repeatable read"
         and db.execute(text("SHOW transaction_read_only")).scalar_one() == "on", "V3 capture requires read-only repeatable-read")
    need(_identity(db) == source_identity and sql_audit_v3.schema_sha256(db) == schema_sha256, "V3 Source identity or schema differs")
    data = {}
    for label, (table, order, limit) in TABLES.items():
        checkpoint()
        data[label] = db.execute(text(f"SELECT row_to_json(t) FROM public.{table} t ORDER BY {order} LIMIT {limit}")).scalars().all()
    need(len(data["generation"]) == 1 and type(data["generation"][0].get("writer_oid")) is int, "V3 generation writer identity unavailable")
    writer_role = db.execute(text("SELECT rolname FROM pg_catalog.pg_roles WHERE oid=:oid"), {"oid": data["generation"][0]["writer_oid"]}).scalar_one()
    structure = capture_structure(db, writer_role)
    need(sql_audit_v3.schema_sha256(db) == schema_sha256 and _identity(db) == source_identity, "V3 Source schema or identity changed during snapshot")
    payload = {"format": FORMAT, "source_identity": source_identity, "schema_sha256": schema_sha256,
               "snapshot_sha256": hashlib.sha256(snapshot.encode()).hexdigest(), "data": data, "structure": structure}
    validate(payload)
    need(data["generation"][0]["epoch"] == epoch and data["generation"][0]["genesis_sha"] == genesis_sha256, "V3 generation pin differs")
    return payload


class AuditSnapshotCapture:
    """A single-use, root-private AES artifact producer called by backup.dump."""

    def __init__(self, *, output, key, source_identity, schema_sha256, epoch, genesis_sha256, root_bridge=None):
        self.output = Path(output)
        need(self.output.is_absolute() and self.output.resolve() == self.output and self.output.is_dir(), "Private V3 capture directory required")
        st = self.output.stat()
        need(st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o700, "Root-private V3 capture directory required")
        need(type(key) is bytes and len(key) in (16, 24, 32), "Archive AES key required")
        need(type(source_identity) is dict and all(type(x) is str and sql_audit_v3.HEX64.fullmatch(x) for x in (schema_sha256, genesis_sha256)) and type(epoch) is str and sql_audit_v3.HEX32.fullmatch(epoch), "Pinned V3 Source scope required")
        self.key = key
        self.identity = source_identity
        self.schema_sha256 = schema_sha256
        self.epoch = epoch
        self.genesis = genesis_sha256
        if root_bridge is not None:
            from .sql_audit_v3_snapshot_bridge import RootSnapshotBridge

            need(type(root_bridge) is RootSnapshotBridge, "Exact Source-root snapshot bridge required")
        self.root_bridge = root_bridge
        self.reference = None

    def capture(self, db, snapshot, *, checkpoint):
        need(self.reference is None and db.dialect.name == "postgresql" and type(snapshot) is str and 0 < len(snapshot) <= 256, "Live single-use PostgreSQL snapshot required")
        if self.root_bridge is None:
            payload = snapshot_payload(db, snapshot, source_identity=self.identity, schema_sha256=self.schema_sha256,
                                       epoch=self.epoch, genesis_sha256=self.genesis, checkpoint=checkpoint)
        else:
            payload = self.root_bridge.capture(snapshot, reader_db=db, reader_identity=self.identity,
                                               schema_sha256=self.schema_sha256, epoch=self.epoch,
                                               genesis_sha256=self.genesis, checkpoint=checkpoint)
        raw = encode(payload)
        plaintext, sealed, verify = (self.output / name for name in ("sql-audit-v3.plain", "sql-audit-v3.bin", "sql-audit-v3.verify"))
        try:
            with plaintext.open("xb") as stream:
                os.chmod(plaintext, 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            ref = backup.encrypt(plaintext, sealed, self.key, checkpoint=checkpoint)
            backup.decrypt(sealed, verify, self.key, ref["plaintext_sha256"], checkpoint=checkpoint)
            need(verify.read_bytes() == raw and checksum(sealed) == ref["sha256"], "V3 encrypted local readback differs")
            checkpoint()
            self.reference = {"format": FORMAT, "epoch": self.epoch, "genesis_sha256": self.genesis, "schema_sha256": self.schema_sha256, "snapshot_sha256": payload["snapshot_sha256"], "file": ref}
            return self.reference
        finally:
            plaintext.unlink(missing_ok=True)
            verify.unlink(missing_ok=True)


def root_snapshot_capture(output, key, *, profile_path, profile_sha256):
    """Build the explicit production bridge from immutable root-owned inputs."""
    from app.raw_archive.scientific_scope_control import _private_json

    from .sql_audit_v3_snapshot_bridge import RootSnapshotBridge

    need(os.geteuid() == 0 and os.getenv("RAW_ARCHIVE_ENVIRONMENT") == "production"
         and os.getenv("RAW_ARCHIVE_WORKER_ROLE") == "archive"
         and os.getenv("TIERED_ARCHIVE_SQL_V3_SOURCE_SNAPSHOT_ENABLED") == "true",
         "Explicit production Source-root V3 backup required")
    value = _private_json(profile_path, profile_sha256, limit=16384)
    fields = {"format", "source_identity", "schema_sha256", "epoch", "genesis_sha256", "binding_path", "binding_sha256",
              "dba_identity_path", "dba_identity_sha256", "challenge_file", "frame_file", "source_manifest_sha256", "producer_sha256",
              "pg_client_dir", "pg_client_manifest_sha256", "crypto_runtime_dir", "crypto_manifest_sha256"}
    need(set(value) == fields and value["format"] == "greenmind.sql-audit-v3-root-snapshot-profile.v1"
         and type(value["source_identity"]) is dict and type(value["epoch"]) is str and sql_audit_v3.HEX32.fullmatch(value["epoch"])
         and all(type(value[k]) is str and sql_audit_v3.HEX64.fullmatch(value[k]) for k in
                 ("schema_sha256", "genesis_sha256", "binding_sha256", "dba_identity_sha256", "source_manifest_sha256", "producer_sha256"))
         and all(type(value[k]) is str and Path(value[k]).is_absolute() for k in ("binding_path", "dba_identity_path", "challenge_file", "frame_file")),
         "Exact V3 Source snapshot profile required")
    bridge = RootSnapshotBridge(binding_path=value["binding_path"], binding_sha256=value["binding_sha256"],
                                dba_identity_path=value["dba_identity_path"], dba_identity_sha256=value["dba_identity_sha256"],
                                challenge_file=value["challenge_file"], frame_file=value["frame_file"],
                                source_manifest_sha256=value["source_manifest_sha256"], producer_sha256=value["producer_sha256"],
                                pg_client_dir=value["pg_client_dir"], pg_client_manifest_sha256=value["pg_client_manifest_sha256"],
                                crypto_runtime_dir=value["crypto_runtime_dir"], crypto_manifest_sha256=value["crypto_manifest_sha256"])
    return AuditSnapshotCapture(output=output, key=key, source_identity=value["source_identity"],
                                schema_sha256=value["schema_sha256"], epoch=value["epoch"],
                                genesis_sha256=value["genesis_sha256"], root_bridge=bridge)


def verify_remote(verifier, reference, key, directory, *, checkpoint):
    """Read independently with the real read-only transport; not a restore proof."""
    from app.raw_archive.read_only_box import CatalogReadOnlyBox

    from .storage import Store, validate_key

    need(type(verifier) is Store and type(verifier.transport) is CatalogReadOnlyBox, "Independent V3 audit read-only transport required")
    need(type(reference) is dict and set(reference) == {"format", "epoch", "genesis_sha256", "schema_sha256", "snapshot_sha256", "file", "remote_key"} and reference["format"] == FORMAT, "Exact V3 audit reference required")
    validate_key(reference["remote_key"])
    ref = reference["file"]
    need(type(ref) is dict and set(ref) == {"format", "sha256", "bytes", "plaintext_sha256"} and ref["format"] == "bin" and type(ref["bytes"]) is int and 0 < ref["bytes"] <= sql_audit_v3.MAX_SNAPSHOT_BYTES + 128 and all(type(ref[k]) is str and sql_audit_v3.HEX64.fullmatch(ref[k]) for k in ("sha256", "plaintext_sha256")), "V3 encrypted object reference differs")
    directory = Path(directory)
    need(directory.is_absolute() and directory.resolve() == directory and directory.is_dir() and directory.stat().st_uid == 0 and stat.S_IMODE(directory.stat().st_mode) == 0o700, "Root-private V3 readback directory required")
    sealed, plaintext = directory / "sql-v3-readback.bin", directory / "sql-v3-readback.plain"
    try:
        checkpoint()
        verifier.download(reference["remote_key"], sealed, ref["bytes"])
        need(sealed.stat().st_size == ref["bytes"] and checksum(sealed) == ref["sha256"], "V3 independent readback differs")
        backup.decrypt(sealed, plaintext, key, ref["plaintext_sha256"], checkpoint=checkpoint)
        need(plaintext.stat().st_size <= sql_audit_v3.MAX_SNAPSHOT_BYTES, "V3 decrypted snapshot exceeds budget")
        payload = validate(decode(plaintext.read_bytes()))
        need(payload["snapshot_sha256"] == reference["snapshot_sha256"] and payload["schema_sha256"] == reference["schema_sha256"] and payload["data"]["generation"][0]["epoch"] == reference["epoch"] and payload["data"]["generation"][0]["genesis_sha"] == reference["genesis_sha256"], "V3 readback binding differs")
        checkpoint()
        return payload
    finally:
        sealed.unlink(missing_ok=True)
        plaintext.unlink(missing_ok=True)


def verify_gateway_bundle(candidate, ledger, verifier, key, *, checkpoint, _include_payload=False):
    """Independently read the actual Gateway receipt and authenticate its V3 audit.

    This establishes readback only. It does not establish native restore, an
    accepted managed snapshot, or permission to delete.
    """
    import tempfile

    from app.raw_archive.read_only_box import CatalogReadOnlyBox

    from . import codec, parquet, pipeline, schema_provider
    from .ledger import Ledger
    from .policy import POLICY_SHA256
    from .storage import Store

    need(type(ledger) is Ledger and type(verifier) is Store and type(verifier.transport) is CatalogReadOnlyBox and callable(checkpoint), "Independent Gateway audit verifier required")
    candidate.validate()
    need(candidate.source == "gateway" and candidate.dataset == "database_backup", "Exact Gateway backup candidate required")
    receipt = ledger.get(candidate.identity)
    need(type(receipt) is dict and receipt.get("state") in {"published", "verified"} and receipt.get("destination") == verifier.identity, "Existing Gateway backup receipt required")
    refs = receipt.get("files")
    manifest_ref = receipt.get("manifest")
    need(type(refs) is list and len(refs) == 6 and all(type(ref) is dict for ref in refs)
         and type(manifest_ref) is dict and type(manifest_ref.get("bytes")) is int
         and 0 < manifest_ref["bytes"] <= 1024 * 1024
         and type(refs[5].get("bytes")) is int and 0 < refs[5]["bytes"] <= sql_audit_v3.MAX_SNAPSHOT_BYTES + 128
         and refs[0].get("backup_format") == schema_provider.AUDIT_V3_FORMAT,
         "Explicit bounded V3 Gateway bundle required")
    with tempfile.TemporaryDirectory(prefix="sql-v3-gateway-", dir=ledger.root) as folder:
        root = Path(folder)
        manifest = root / "manifest.json"
        checkpoint()
        verifier.download(manifest_ref["key"], manifest, manifest_ref["bytes"])
        need(manifest.stat().st_size == manifest_ref["bytes"] and checksum(manifest) == manifest_ref["sha256"] and manifest.stat().st_size <= 1024 * 1024, "V3 Gateway manifest differs")
        try:
            manifest_data = json.loads(manifest.read_bytes())
        except ValueError as exc:
            raise ArchiveBlocked("V3 Gateway manifest encoding differs") from exc
        need(manifest_data == {"version": 1, "policy": POLICY_SHA256, "candidate": pipeline.candidate_json(candidate), "identity": candidate.identity, "files": refs}, "V3 Gateway manifest binding differs")
        paths = []
        for index, ref in enumerate(refs):
            checkpoint()
            path = root / f"{index:04d}.bin"
            verifier.download(ref["key"], path, ref["bytes"])
            pipeline.verify_file(path, ref, checkpoint)
            paths.append(path)
        rows = list(parquet.read(paths[0], refs[0], checkpoint=checkpoint))
        need(len(rows) == 1 and rows[0]["identity"] == codec.digest(rows[0]["body"]) and rows[0]["organization"] == candidate.organization, "V3 Gateway index differs")
        body = rows[0]["body"]
        need(type(body) is dict and set(body) == {"backup_format", "source", "at", "files", "master_proof", "audit_v3"} and body["backup_format"] == schema_provider.AUDIT_V3_FORMAT and body["source"] == "gateway" and body["at"] == candidate.end, "V3 Gateway body differs")
        bare_refs = [{k: v for k, v in ref.items() if k != "key"} for ref in refs[1:]]
        need(body["files"] == bare_refs, "V3 Gateway encrypted file inventory differs")
        proof = body["master_proof"]
        schema_provider.validate_proof(proof, "gateway")
        descriptor = schema_provider.descriptor(proof, "gateway", bare_refs[:4])
        descriptor["backup_format"] = schema_provider.AUDIT_V3_FORMAT
        descriptor["audit_v3_plaintext_sha256"] = bare_refs[4]["plaintext_sha256"]
        need(all(refs[0].get(k) == value for k, value in descriptor.items()), "V3 Gateway descriptor differs")
        audit_ref = body["audit_v3"]
        need(type(audit_ref) is dict and set(audit_ref) == {"format", "epoch", "genesis_sha256", "schema_sha256", "snapshot_sha256", "file"} and audit_ref["format"] == FORMAT and audit_ref["file"] == bare_refs[4], "V3 audit file binding differs")
        need(all(refs[0].get("audit_v3_" + name) == audit_ref[name] for name in ("epoch", "genesis_sha256", "schema_sha256", "snapshot_sha256")), "V3 audit primary descriptor differs")
        auth_file, audit_file = root / "provider-auth.json", root / "audit.json"
        backup.decrypt(paths[4], auth_file, key, bare_refs[3]["plaintext_sha256"], checkpoint=checkpoint)
        need(auth_file.stat().st_size <= backup.AUTH_MAX_BYTES, "V3 provider auth exceeds budget")
        auth = codec.decode(auth_file.read_bytes())
        expected_auth = {"schema": schema_provider.AUDIT_V3_AUTH_FORMAT, "format": schema_provider.AUDIT_V3_FORMAT, "source": "gateway", "environment": candidate.environment, "version": schema_provider.proof_version("gateway"), "captured_at": candidate.end, "master_proof": proof, "dump_files": bare_refs[:3], "audit_v3": audit_ref}
        need(auth == expected_auth, "V3 encrypted Gateway authentication differs")
        backup.decrypt(paths[5], audit_file, key, bare_refs[4]["plaintext_sha256"], checkpoint=checkpoint)
        payload = validate(decode(audit_file.read_bytes()))
        generation = payload["data"]["generation"][0]
        need(payload["schema_sha256"] == audit_ref["schema_sha256"] and payload["snapshot_sha256"] == audit_ref["snapshot_sha256"] and generation["epoch"] == audit_ref["epoch"] and generation["genesis_sha"] == audit_ref["genesis_sha256"], "V3 Gateway audit binding differs")
        evidence = {"format": "greenmind.sql-audit-v3-independent-readback.v1", "candidate": candidate.identity, "gateway_manifest_sha256": manifest_ref["sha256"], "audit_plaintext_sha256": bare_refs[4]["plaintext_sha256"], "unknown": bool(payload["data"]["unknown"]), "restore_verified": False}
        return (evidence, payload) if _include_payload else evidence


def record_gateway_readback(candidate, ledger, verifier, key, *, checkpoint):
    """Persist references only after actual independent readback; no readiness flag."""
    from app.raw_archive.coordination import bound_job_lock
    from app.raw_archive.remote_lease import require_bound_source_lease

    from . import registry_v5

    bound_job_lock()
    require_bound_source_lease()
    evidence = verify_gateway_bundle(candidate, ledger, verifier, key, checkpoint=checkpoint)
    need(not evidence["unknown"], "Unknown Source DELETE permanently holds V3 scope")
    require_bound_source_lease()
    receipt = ledger.get(candidate.identity)
    need(type(receipt) is dict and receipt.get("manifest", {}).get("sha256") == evidence["gateway_manifest_sha256"], "Gateway receipt changed during readback")
    refs = receipt["files"]
    row = [refs[0]["audit_v3_epoch"], candidate.identity, evidence["gateway_manifest_sha256"], refs[5]["sha256"], evidence["audit_plaintext_sha256"]]
    registry_v5.validate("sql_audit_v3_backup", row, candidate.environment, ledger)
    need(not ledger.db.in_transaction, "Idle V3 audit backup journal required")
    try:
        ledger.db.execute("BEGIN IMMEDIATE")
        ledger.db.execute(registry_v5.DDL["sql_audit_v3_backup"])
        ledger.db.execute(registry_v5.DDL["sql_audit_v3_backup_history"])
        registry_v5.schema(ledger.db, "sql_audit_v3_backup")
        registry_v5.schema(ledger.db, "sql_audit_v3_backup_history")
        previous = ledger.db.execute("SELECT epoch,candidate,gateway_manifest_sha256,audit_sha256,plaintext_sha256 FROM sql_audit_v3_backup WHERE epoch=?", (row[0],)).fetchone()
        for entry in ([list(previous)] if previous is not None else []) + [row]:
            registry_v5.validate("sql_audit_v3_backup_history", entry, candidate.environment, ledger)
            historical = ledger.db.execute("SELECT epoch,candidate,gateway_manifest_sha256,audit_sha256,plaintext_sha256 FROM sql_audit_v3_backup_history WHERE epoch=? AND candidate=?", (entry[0], entry[1])).fetchone()
            need(historical is None or tuple(historical) == tuple(entry), "V3 audit backup history changed")
            if historical is None:
                count = ledger.db.execute("SELECT count(*) FROM sql_audit_v3_backup_history WHERE epoch=?", (entry[0],)).fetchone()[0]
                need(count < 128, "V3 audit backup history capacity exhausted")
                ledger.db.execute("INSERT INTO sql_audit_v3_backup_history VALUES(?,?,?,?,?)", entry)
        if previous is None:
            ledger.db.execute("INSERT INTO sql_audit_v3_backup VALUES (?,?,?,?,?)", row)
        elif tuple(previous) != tuple(row):
            scope_exists = ledger.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sql_audit_v3_scope'").fetchone()
            if scope_exists:
                registry_v5.schema(ledger.db, "sql_audit_v3_scope")
                scope = ledger.db.execute("SELECT epoch,state FROM sql_audit_v3_scope WHERE id=1").fetchone()
                need(scope is not None and scope[0] == row[0] and scope[1] in {"current", "needs_reconcile"}, "V3 scope generation differs during backup rotation")
                changed = ledger.db.execute("UPDATE sql_audit_v3_scope SET state='needs_reconcile' WHERE id=1 AND epoch=? AND state=?", tuple(scope)).rowcount
                need(changed == 1, "V3 backup rotation scope CAS failed")
            changed = ledger.db.execute("UPDATE sql_audit_v3_backup SET candidate=?,gateway_manifest_sha256=?,audit_sha256=?,plaintext_sha256=? WHERE epoch=? AND candidate=? AND gateway_manifest_sha256=? AND audit_sha256=? AND plaintext_sha256=?", (*row[1:], *previous)).rowcount
            need(changed == 1, "V3 backup rotation CAS failed")
        checkpoint()
        require_bound_source_lease()
        ledger.db.commit()
    except BaseException:
        ledger.db.rollback()
        raise
    ledger.publish_read_view()
    return {**evidence, "registry_reference_recorded": True, "restore_verified": False}


def verify_native_rows(database_url, target, payload, *, checkpoint, target_writer_role=None):
    """Compare native rows in a marked isolated restore DB; never writes to it.

    A native row match is deliberately not a complete schema/role/restore proof.
    """
    from sqlalchemy import create_engine

    from .database_restore import _provider_target_guard, _provider_target_identity
    from .recovery import restore_url

    validate(payload)
    need(not payload["data"]["unknown"], "Unknown Source DELETE holds V3 restore")
    url = restore_url(database_url)
    _provider_target_guard(target, url)
    source = payload["source_identity"]
    need(source.get("database") != url.database, "Restore target must differ from Source")
    expected_types = {
        "generation": [("singleton", "boolean"), ("epoch", "text"), ("genesis_sha", "text"), ("writer_oid", "oid")],
        "approvals": [("slot", "smallint"), ("epoch", "text"), ("writer_oid", "oid"), ("candidate_sha", "text"), ("tenant_sha", "text"), ("pk_sha", "text"), ("permit_sha", "text"), ("body", "text"), ("expected", "text[]"), ("start_at", "timestamp with time zone"), ("expires_at", "timestamp with time zone")],
        "events": [("slot", "smallint"), ("txid", "bigint"), ("created_at", "timestamp with time zone")],
        "rows": [("slot", "smallint"), ("hash", "text")],
        "unknown": [("singleton", "boolean"), ("txid", "bigint"), ("created_at", "timestamp with time zone")],
    }
    engine = create_engine(url, pool_size=1, max_overflow=0)
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as db, db.begin():
            db.execute(text("SET TRANSACTION READ ONLY"))
            db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            _provider_target_identity(db, target, url, "off")
            for label, (table, order, limit) in TABLES.items():
                checkpoint()
                columns = db.execute(text("""SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod)
                    FROM pg_catalog.pg_attribute a WHERE a.attrelid=pg_catalog.to_regclass(:name)
                    AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum"""), {"name": "public." + table}).all()
                need([tuple(row) for row in columns] == expected_types[label], "V3 restore native types differ")
                actual = db.execute(text(f"SELECT row_to_json(t) FROM public.{table} t ORDER BY {order} LIMIT {limit}")).scalars().all()
                need(actual == payload["data"][label], "V3 restore native rows differ")
            structure_match = False
            if target_writer_role is not None:
                need(type(target_writer_role) is str and sql_audit_v3.NAME.fullmatch(target_writer_role), "Exact isolated V3 writer role required")
                target_oid = db.execute(text("SELECT oid::bigint FROM pg_catalog.pg_roles WHERE rolname=:role"), {"role": target_writer_role}).scalar_one()
                need(target_oid != payload["data"]["generation"][0]["writer_oid"], "Restore writer role must have an independent OID")
                for _label, (table, _order, _limit) in TABLES.items():
                    owner = db.execute(text("SELECT relowner::bigint FROM pg_catalog.pg_class WHERE oid=pg_catalog.to_regclass(:name)"), {"name": "public." + table}).scalar_one()
                    need(owner == target["owner_oid"], "V3 restored table owner differs")
                need(capture_structure(db, target_writer_role) == payload["structure"], "V3 restored native schema, functions, triggers or grants differ")
                structure_match = True
            checkpoint()
            _provider_target_identity(db, target, url, "off")
    finally:
        engine.dispose()
    return {"format": "greenmind.sql-audit-v3-native-row-check.v1", "snapshot_sha256": payload["snapshot_sha256"], "native_rows_match": True, "target_schema_grants_match": structure_match, "restore_verified": False}
