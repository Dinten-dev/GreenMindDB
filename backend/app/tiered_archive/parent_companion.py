"""Quarantined faithful parents in existing AES/bin/Parquet/manifest pipeline.

No Source fallback, new encoding, source mutation, grant or acceptance flag.
The caller owns Root leases, guarded target transport and durable native receipts.
"""

import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from app.raw_archive import parent_capture as native
from app.raw_archive import scientific_parts
from app.raw_archive.policy import ArchiveBlocked, checksum
from app.raw_archive.scientific_capture import HEADER, digest_json

from . import backup, codec, parquet, pipeline
from .policy import Candidate
from .recovery import restore_url
from .runtime import candidate_from_json

SYSTEM_ORG = "00000000-0000-0000-0000-000000000000"


def need(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


def _private(path):
    import stat

    path = Path(path)
    s = path.lstat()
    need(
        path.is_absolute()
        and path.resolve() == path
        and stat.S_ISDIR(s.st_mode)
        and s.st_uid == os.geteuid()
        and stat.S_IMODE(s.st_mode) == 0o700,
        "Private parent work directory required",
    )
    return path


def _reserve(path):
    need(
        shutil.disk_usage(path).free >= 2499805184 + 4 * native.MAX_PART_BYTES,
        "Parent archive disk reserve unavailable",
    )


def stage(
    proof,
    capture_sha256,
    index,
    position,
    received,
    key,
    output,
    *,
    source_proof_sha256,
    checkpoint,
):
    """Seal ONE actual native received part; Source full plaintext stays Source."""
    native.validate_index(proof, index, capture_sha256)
    need(
        digest_json(proof) == capture_sha256 and re.fullmatch(r"[a-f0-9]{64}", source_proof_sha256),
        "Pinned actual parent Source completion required",
    )
    need(type(position) is int and 0 <= position < len(index["parts"]), "Parent position differs")
    part = index["parts"][position]
    received, output = Path(received), Path(output)
    _private(output.parent)
    _reserve(output.parent)
    need(not os.path.lexists(output), "New parent sealed stage required")
    native.file_identity(received)
    need(
        received.stat().st_size == part["bytes"] and checksum(received) == part["sha256"],
        "Received parent bytes differ",
    )
    output.mkdir(mode=0o700)
    ref = backup.encrypt(received, output / "0001.bin", key, checkpoint=checkpoint)
    with tempfile.TemporaryDirectory(prefix="parent-auth-", dir=output) as folder:
        backup.decrypt(
            output / "0001.bin", Path(folder) / "native", key, part["sha256"], checkpoint=checkpoint
        )
    body = {
        "source": proof["kind"],
        "capture": proof,
        "capture_sha256": capture_sha256,
        "index": index,
        "index_sha256": digest_json(index),
        "position": position,
        "source_proof_sha256": source_proof_sha256,
        "native": ref,
        "quarantined": True,
        "science_compatibility_proven": False,
    }
    start, end = (datetime.fromisoformat(proof[k]) for k in ("started_at", "finished_at"))
    row = {
        "identity": codec.digest(body),
        "organization": SYSTEM_ORG,
        "sensor": part["table"],
        "time_us": codec.epoch_us(end),
        "body": body,
    }
    index_ref = dict(
        parquet.write(output / "0000.parquet", [row], checkpoint=checkpoint), format="parquet"
    )
    candidate = Candidate(
        "production",
        proof["kind"],
        "scientific_parent_context",
        SYSTEM_ORG,
        start,
        end,
        index_ref["content_sha256"],
        1,
        "features",
        end,
        quarantined=True,
    )
    candidate.validate()
    stage_body = {"candidate": pipeline.candidate_json(candidate), "files": [index_ref, ref]}
    native._save(output / "stage.json", stage_body)
    return {
        "stage": str(output),
        "candidate": candidate.identity,
        "position": position,
        "quarantined": True,
        "published": False,
    }


def publish_stage(output, store, ledger, *, checkpoint):
    output = _private(output)
    _reserve(output)
    raw = output / "stage.json"
    native.file_identity(raw)
    need(raw.stat().st_size <= 65536, "Parent stage bound exceeded")
    body = json.loads(raw.read_bytes())
    candidate = candidate_from_json(body["candidate"])
    _candidate(candidate)
    files = body["files"]
    need(
        len(files) == 2 and [f["format"] for f in files] == ["parquet", "bin"],
        "Complete sealed parent files required",
    )
    paths = [output / "0000.parquet", output / "0001.bin"]
    for path, ref in zip(paths, files, strict=True):
        pipeline.verify_file(path, ref, checkpoint)
    return pipeline.publish(
        candidate, list(zip(paths, files, strict=True)), store, ledger, checkpoint=checkpoint
    )


def _candidate(candidate):
    candidate.validate()
    need(
        candidate.dataset == "scientific_parent_context"
        and candidate.environment == "production"
        and candidate.organization == SYSTEM_ORG
        and candidate.quarantined is True
        and candidate.kind == "features"
        and candidate.rows == 1
        and not any(
            (
                candidate.complete,
                candidate.features_verified,
                candidate.projections_verified,
                candidate.dependencies_complete,
            )
        ),
        "Exact quarantined parent candidate required",
    )


def readback(
    identity,
    ledger,
    publisher,
    verifier,
    key,
    workdir,
    *,
    checkpoint,
    provider_proof_path,
    provider_proof_sha256,
    provider_terminal_path,
    provider_terminal_sha256,
    consumer=None,
):
    """GET manifest/index/native through independent provider-proved RO identity.

    consumer(body, plaintext_path) runs only after complete AES/SHA verification;
    it may perform the isolated target restore. Plaintext is never returned.
    """
    from .policy import POLICY_SHA256
    from .scientific_registry_import import _provider_proof, _transport

    _transport(publisher, verifier)
    _provider_proof(
        provider_proof_path, provider_proof_sha256, provider_terminal_path, provider_terminal_sha256
    )
    receipt = ledger.get(identity)
    need(
        receipt
        and receipt["state"] in {"published", "verified"}
        and receipt["destination"] == verifier.identity,
        "Actual parent archive receipt required",
    )
    candidate = candidate_from_json(receipt["source"])
    _candidate(candidate)
    need(candidate.identity == identity and len(receipt["files"]) == 2, "Parent receipt differs")
    workdir = _private(workdir)
    _reserve(workdir)
    with tempfile.TemporaryDirectory(prefix="parent-ro-readback-", dir=workdir) as directory:
        root = Path(directory)
        manifest = root / "manifest.json"
        ref = receipt["manifest"]
        need(0 < ref["bytes"] <= 1024**2, "Parent manifest bound exceeded")
        verifier.download(ref["key"], manifest, ref["bytes"])
        pipeline.verify_file(manifest, ref, checkpoint)
        need(
            json.loads(manifest.read_bytes())
            == {
                "version": 1,
                "policy": POLICY_SHA256,
                "candidate": pipeline.candidate_json(candidate),
                "identity": identity,
                "files": receipt["files"],
            },
            "Parent manifest binding differs",
        )
        refs = receipt["files"]
        need(
            [f["format"] for f in refs] == ["parquet", "bin"]
            and 0 < refs[0]["bytes"] <= 1024**2
            and 0 < refs[1]["bytes"] <= native.MAX_PART_BYTES + 1024,
            "Parent encrypted workset exceeds bound",
        )
        parquet_path = root / "index.parquet"
        verifier.download(refs[0]["key"], parquet_path, refs[0]["bytes"])
        rows = list(parquet.read(parquet_path, refs[0], checkpoint=checkpoint))
        need(
            len(rows) == 1
            and rows[0]["organization"] == SYSTEM_ORG
            and rows[0]["identity"] == codec.digest(rows[0]["body"])
            and refs[0]["content_sha256"] == candidate.content_sha256,
            "Parent index binding differs",
        )
        body = rows[0]["body"]
        proof, index = body["capture"], body["index"]
        native.validate_index(proof, index, body["capture_sha256"])
        need(
            digest_json(proof) == body["capture_sha256"]
            and digest_json(index) == body["index_sha256"]
            and proof["kind"] == candidate.source
            and body["source"] == candidate.source
            and body["quarantined"] is True
            and body["science_compatibility_proven"] is False
            and type(body["position"]) is int
            and 0 <= body["position"] < len(index["parts"])
            and body["native"] == {k: v for k, v in refs[1].items() if k != "key"},
            "Parent capture/index/native reference differs",
        )
        part = index["parts"][body["position"]]
        need(body["native"]["plaintext_sha256"] == part["sha256"], "Parent native SHA differs")
        encrypted, plaintext = root / "part.bin", root / "part.native"
        verifier.download(refs[1]["key"], encrypted, refs[1]["bytes"])
        pipeline.verify_file(encrypted, refs[1], checkpoint)
        backup.decrypt(encrypted, plaintext, key, part["sha256"], checkpoint=checkpoint)
        need(
            plaintext.stat().st_size == part["bytes"] and ledger.get(identity) == receipt,
            "Parent plaintext or archive receipt changed",
        )
        result = consumer(body, plaintext) if consumer else None
        return {
            "candidate": identity,
            "position": body["position"],
            "capture_sha256": body["capture_sha256"],
            "rows": part["rows"],
            "native_sha256": part["sha256"],
            "source_data_accesses": 0,
            "independent_readback": True,
            "restore": result,
            "quarantined": True,
            "archive_accepted": False,
            "science_compatibility_proven": False,
        }


def _target(db, engine, target, marker_sha256, source, checkpoint, target_guard):
    url = restore_url(engine.url)
    need(
        type(target) is dict
        and set(target)
        == {
            "database",
            "database_oid",
            "role",
            "role_oid",
            "port",
            "server_address",
            "postmaster_start",
        }
        and type(marker_sha256) is str
        and re.fullmatch(r"[a-f0-9]{64}", marker_sha256),
        "Exact parent target and marker pins required",
    )
    need(
        url.host == "127.0.0.1"
        and url.port == 19442
        and url.username == target["role"]
        and bool(url.password)
        and url.database == target["database"]
        and re.fullmatch(r"gm_native_restore_[a-f0-9]{12,32}_" + source, url.database),
        "Isolated actual parent target required",
    )
    checkpoint()
    target_guard()
    actual = dict(
        db.execute(
            text("""SELECT current_database() AS database,
        (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS database_oid,
        current_user AS role,(SELECT oid::bigint FROM pg_roles WHERE rolname=current_user) AS role_oid,
        inet_server_port() AS port,inet_server_addr()::text AS server_address,
        pg_postmaster_start_time()::text AS postmaster_start,
        (SELECT datdba::bigint FROM pg_database WHERE datname=current_database()) AS owner_oid,
        current_user=session_user AS same_role,
        (SELECT rolcanlogin AND NOT(rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
          FROM pg_roles WHERE rolname=current_user) AS unprivileged,
        current_setting('server_version_num')::int AS version,
        current_setting('session_replication_role') AS replication_role,
        has_database_privilege('public',current_database(),'CONNECT') AS public_connect,
        has_database_privilege('public',current_database(),'TEMPORARY') AS public_temp,
        encode(sha256(convert_to(coalesce((SELECT shobj_description(oid,'pg_database') FROM pg_database
          WHERE datname=current_database()),''),'UTF8')),'hex') AS marker_sha256""")
        )
        .mappings()
        .one()
    )
    need(
        all(actual[k] == v for k, v in target.items())
        and actual["owner_oid"] == target["role_oid"]
        and actual["same_role"]
        and actual["unprivileged"]
        and actual["version"] == 150010
        and actual["replication_role"] == "origin"
        and actual["public_connect"] is False
        and actual["public_temp"] is False
        and actual["marker_sha256"] == marker_sha256,
        "Parent target identity/owner/marker changed",
    )


def _keys(path, table, expected_rows, checkpoint):
    columns = table["columns"]
    pk = columns[0]["name"]
    kind = columns[0]["type"]
    need(
        pk in {"id", "wav_id", "segment_id"} and kind in {"uuid", "varchar"},
        "Exact native parent PK required",
    )
    keys = []
    with Path(path).open("rb") as stream:
        need(stream.read(len(HEADER)) == HEADER, "Parent native header differs")
        while True:
            checkpoint()
            row = scientific_parts._row(stream, len(columns))
            if row is None:
                need(not stream.read(1), "Parent native trailer differs")
                break
            import struct

            size = struct.unpack("!i", row[2:6])[0]
            need(0 < size <= 128, "Parent native primary key differs")
            raw = row[6 : 6 + size]
            keys.append(str(uuid.UUID(bytes=raw)) if kind == "uuid" else raw.decode("utf-8"))
    need(
        len(keys) == len(set(keys)) == expected_rows <= native.MAX_PART_ROWS,
        "Parent PK coverage differs",
    )
    return pk, kind, keys


def restore_part(
    body,
    plaintext,
    engine,
    target,
    marker_sha256,
    *,
    checkpoint,
    target_guard,
    audit_only=False,
    master_contract=None,
):
    """Faithful actual-table COPY. Replay proves exact bytes, never supplies defaults.

    The caller journals Root-owned completion/owned-wait evidence. A lost result is
    reconciled by this exact row/count/digest replay; no Source original is changed.
    Parent loading must precede creation/freezing of the existing master baseline.
    """
    proof, index, position = body["capture"], body["index"], body["position"]
    native.validate_index(proof, index, body["capture_sha256"])
    need(
        digest_json(proof) == body["capture_sha256"] and digest_json(index) == body["index_sha256"],
        "Parent restore capture/index binding differs",
    )
    part = index["parts"][position]
    table = next(t for t in native.scope(proof["kind"])["tables"] if t["name"] == part["table"])
    need(checksum(plaintext) == part["sha256"], "Faithful parent input digest differs")
    pk, kind, keys = _keys(plaintext, table, part["rows"], checkpoint)
    with engine.connect() as db, db.begin():
        db.execute(text("SET LOCAL statement_timeout='8000ms'"))
        db.execute(text("SET LOCAL lock_timeout='150ms'"))
        db.execute(text("SET LOCAL search_path=pg_catalog"))
        _target(db, engine, target, marker_sha256, proof["kind"], checkpoint, target_guard)
        need(
            db.execute(text("SELECT pg_try_advisory_xact_lock(1735157614,19443)")).scalar_one()
            is True,
            "Parent restore target busy",
        )
        for item in native.scope(proof["kind"])["tables"]:
            db.execute(text('LOCK TABLE public."' + item["name"] + '" IN ACCESS EXCLUSIVE MODE'))
            _portable_schema(db, item)
            if item["name"] != part["table"] and any(
                p["table"] == item["name"] for p in index["parts"][:position]
            ):
                expected = next(f["rows"] for f in proof["files"] if f["table"] == item["name"])
                actual = db.execute(
                    text('SELECT count(*) FROM public."' + item["name"] + '"')
                ).scalar_one()
                need(actual == expected, "Previous parent table is incomplete")
        snapshot = _master_anchor(db, proof, master_contract) if audit_only else None
        prefix = sum(p["rows"] for p in index["parts"][:position] if p["table"] == part["table"])
        count = db.execute(text('SELECT count(*) FROM public."' + part["table"] + '"')).scalar_one()
        if audit_only:
            expected = next(f["rows"] for f in proof["files"] if f["table"] == part["table"])
            need(count == expected, "Final parent table row total differs")
        else:
            need(
                count in {prefix, prefix + part["rows"]},
                "Unjournaled parent table rows or missing part",
            )
        replay = audit_only or count == prefix + part["rows"]
        columns = ",".join('"' + c["name"] + '"' for c in table["columns"])
        cursor = db.connection.driver_connection.cursor()
        try:
            if not replay:
                from app.raw_archive.scientific_restore import _Reader

                with Path(plaintext).open("rb") as stream:
                    cursor.copy_expert(
                        'COPY public."'
                        + part["table"]
                        + '" ('
                        + columns
                        + ") FROM STDIN (FORMAT binary)",
                        _Reader(stream, checkpoint),
                    )
            with tempfile.TemporaryDirectory(
                prefix="parent-sql-readback-", dir=Path(plaintext).parent
            ) as directory:
                out = Path(directory) / "native"
                _sql_readback(cursor, table, pk, kind, keys, out, checkpoint)
                need(
                    out.stat().st_size == part["bytes"] and checksum(out) == part["sha256"],
                    "Restored operational parent values differ",
                )
        finally:
            cursor.close()
        _target(db, engine, target, marker_sha256, proof["kind"], checkpoint, target_guard)
        if audit_only:
            need(
                _master_anchor(db, proof, master_contract)["master_epoch"]
                == snapshot["master_epoch"],
                "Parent master epoch changed during content audit",
            )
    return {
        "target": target,
        "capture_sha256": body["capture_sha256"],
        "index_sha256": body["index_sha256"],
        "position": position,
        "table": part["table"],
        "rows": part["rows"],
        "native_sha256": part["sha256"],
        "replayed": replay,
        "audit_only": audit_only,
        "master_epoch": snapshot["master_epoch"] if snapshot else None,
        "masters_sha256": master_contract["masters_sha256"] if snapshot else None,
        "all_captured_columns_preserved": True,
        "source_accesses": 0,
        "science_compatibility_proven": False,
        "archive_accepted": False,
    }


def _master_anchor(db, proof, contract):
    from . import restore_baseline, restore_fence, restore_session, restore_universe

    need(
        type(contract) is dict and set(contract) == {"plan", "plan_sha256", "masters_sha256"},
        "Actual existing parent master baseline contract required",
    )
    restore_session.validate_plan(contract["plan"], contract["plan_sha256"])
    need(
        contract["plan"]["source"] == proof["kind"]
        and contract["plan"]["schema_master_sha256"] == proof["schema_master_sha256"]
        and all(
            contract["plan"]["universe"]["tables"].get(t["name"]) == "master"
            for t in native.scope(proof["kind"])["tables"]
        ),
        "Parent baseline source/backup/roles differ",
    )
    restore_universe.bind(db, contract["plan"])
    _, masters = restore_universe.tables(db, contract["plan"])
    snapshot = restore_fence.check(db)
    need(
        contract["masters_sha256"] != "0" * 64
        and restore_baseline.anchor(db, masters, snapshot) == contract["masters_sha256"],
        "Actual parent master baseline changed or incomplete",
    )
    return snapshot


def complete_restore(
    proof,
    capture_sha256,
    index,
    audits,
    engine,
    target,
    marker_sha256,
    master_contract,
    *,
    checkpoint,
    target_guard,
):
    """Join bounded content audits from one unchanged existing master epoch.

    Each audit MUST be a Root-owned native receipt from readback's audit-only
    consumer, not user-provided PASS JSON. The existing fence/baseline is checked
    live again. A later parent mutation invalidates this evidence via master epoch.
    """
    native.validate_index(proof, index, capture_sha256)
    need(
        digest_json(proof) == capture_sha256
        and type(audits) is list
        and len(audits) == len(index["parts"]),
        "Every parent content audit required",
    )
    for position, (part, audit) in enumerate(zip(index["parts"], audits, strict=True)):
        need(
            audit.get("audit_only") is True
            and audit.get("target") == target
            and audit.get("capture_sha256") == capture_sha256
            and audit.get("index_sha256") == digest_json(index)
            and audit.get("position") == position
            and audit.get("native_sha256") == part["sha256"]
            and audit.get("rows") == part["rows"]
            and audit.get("masters_sha256") == master_contract["masters_sha256"],
            "Parent audit target/content/baseline binding differs",
        )
    with engine.connect() as db, db.begin():
        db.execute(text("SET LOCAL statement_timeout='8000ms'"))
        db.execute(text("SET LOCAL lock_timeout='150ms'"))
        db.execute(text("SET LOCAL search_path=pg_catalog"))
        _target(db, engine, target, marker_sha256, proof["kind"], checkpoint, target_guard)
        # Existing fence checks take their own control locks; no application rows
        # are inserted, updated or deleted in this final metadata transaction.
        snapshot = _master_anchor(db, proof, master_contract)
        need(
            all(a["master_epoch"] == snapshot["master_epoch"] for a in audits),
            "Parent audit master epochs differ",
        )
        for table, source in zip(
            native.scope(proof["kind"])["tables"], proof["files"], strict=True
        ):
            _portable_schema(db, table)
            actual = db.execute(
                text('SELECT count(*) FROM public."' + table["name"] + '"')
            ).scalar_one()
            need(actual == source["rows"], "Final complete parent row total differs")
        _target(db, engine, target, marker_sha256, proof["kind"], checkpoint, target_guard)
    return {
        "target": target,
        "capture_sha256": capture_sha256,
        "index_sha256": digest_json(index),
        "parts": len(audits),
        "rows": sum(f["rows"] for f in proof["files"]),
        "masters_sha256": master_contract["masters_sha256"],
        "master_epoch": snapshot["master_epoch"],
        "faithful_parent_restore_verified": True,
        "backup_anchor_reference_only": True,
        "backup_snapshot_consistency_proven": False,
        "master_context_closure_proven": False,
        "science_compatibility_proven": False,
        "same_snapshot_as_previous_science": False,
        "cross_database_atomic": False,
        "archive_accepted": False,
        "source_accesses": 0,
    }


def _portable_schema(db, table):
    """Reuse exact Source schema comparison with target-local reader privileges."""
    columns = [dict(c, reader_column_select=True) for c in table["columns"]]
    # Current unprivileged target owner has SELECT on every own column. All
    # actual types/defaults/constraints/indexes are still compared verbatim.
    native._schema(db, dict(table, columns=columns))
    safe = db.execute(
        text("""SELECT c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user),
        c.relkind='r' AND c.relpersistence='p',NOT c.relrowsecurity AND NOT c.relforcerowsecurity,
        NOT EXISTS(SELECT 1 FROM pg_inherits WHERE inhparent=c.oid OR inhrelid=c.oid),
        NOT EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_constraint k ON k.oid=t.tgconstraint
          WHERE k.conrelid=c.oid AND k.contype='f' AND t.tgenabled NOT IN ('O','A'))
        FROM pg_class c WHERE c.oid=to_regclass(:relation)"""),
        {"relation": "public." + table["name"]},
    ).one()
    need(all(value is True for value in safe), "Untrusted actual parent relation")


def _sql_readback(cursor, table, pk, kind, keys, output, checkpoint):
    from app.raw_archive.scientific_restore import _Sink

    fields = ",".join('t."' + c["name"] + '"' for c in table["columns"])
    query = cursor.mogrify(
        "COPY (SELECT "
        + fields
        + " FROM unnest(%s::"
        + ("uuid" if kind == "uuid" else "text")
        + '[]) WITH ORDINALITY k(id,n) JOIN public."'
        + table["name"]
        + '" t ON t."'
        + pk
        + '"=k.id ORDER BY k.n) TO STDOUT (FORMAT binary)',
        (keys,),
    )
    with output.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        cursor.copy_expert(query, _Sink(stream, native.MAX_PART_BYTES, checkpoint))
        stream.flush()
        os.fsync(stream.fileno())


def science_compatibility(
    db,
    science_capture_sha256,
    *,
    expected_science_capture_sha256,
    schema_master_sha256,
    expected_schema_master_sha256,
):
    """Actual semantic identity gate after both scientific tables are restored.

    Not a shared-snapshot claim. Missing parents, reassigned identity/calibration
    or changed original SHA fail; unknown historical tenant context stays unknown.
    Caller runs this in its already pinned/frozen target transaction.
    """
    need(
        all(
            type(v) is str and re.fullmatch(r"[a-f0-9]{64}", v)
            for v in (
                science_capture_sha256,
                expected_science_capture_sha256,
                schema_master_sha256,
                expected_schema_master_sha256,
            )
        ),
        "Pinned science and backup digests required",
    )
    need(
        science_capture_sha256 == expected_science_capture_sha256
        and schema_master_sha256 == expected_schema_master_sha256,
        "Exact independent science and backup bindings required",
    )
    bad = db.execute(
        text("""SELECT EXISTS(SELECT 1 FROM public.wav_feature f
        LEFT JOIN public.wav_file w ON w.id=f.wav_file_id
        WHERE w.id IS NULL OR f.source_sha256 IS DISTINCT FROM w.content_sha256
          OR f.sensor_id IS DISTINCT FROM w.sensor_id OR f.gateway_id IS DISTINCT FROM w.gateway_id
          OR f.sensor_mac IS DISTINCT FROM w.sensor_mac OR f.started_at IS DISTINCT FROM w.started_at
          OR f.ended_at IS DISTINCT FROM w.ended_at OR f.calibration_version IS DISTINCT FROM w.calibration_version)
        OR EXISTS(SELECT 1 FROM public.wav_feature_version f LEFT JOIN public.wav_file w ON w.id=f.wav_file_id
          WHERE w.id IS NULL OR f.source_sha256 IS DISTINCT FROM w.content_sha256)
        OR EXISTS(SELECT 1 FROM public.visual_wav v LEFT JOIN public.wav_file w ON w.id=v.wav_id
          WHERE w.id IS NULL OR v.source_sha256 IS DISTINCT FROM w.content_sha256)""")
    ).scalar_one()
    need(bad is False, "Frozen science and actual parent identity/calibration differ")
    return {
        "science_compatibility_proven": True,
        "same_snapshot_as_previous_science": False,
        "cross_database_atomic": False,
        "historical_assignment_inferred": False,
        "archive_accepted": False,
    }
