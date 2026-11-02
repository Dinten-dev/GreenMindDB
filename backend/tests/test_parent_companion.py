"""Local AES/Parquet/pipeline/independent-GET tests, no Source fallback."""

import copy
import io
import json
import re
import struct
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from app.raw_archive import parent_capture as native
from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import parent_companion as parent
from app.tiered_archive import parquet, scientific_registry_import
from app.tiered_archive.ledger import Ledger
from tests.raw_archive.test_parent_capture import fixture_capture


class Store:
    identity = "independent-parent-destination"

    def __init__(self, objects):
        self.objects, self.gets = objects, []

    def publish(self, key, path):
        self.objects[key] = Path(path).read_bytes()

    def download(self, key, path, size):
        self.gets.append(key)
        Path(path).write_bytes(self.objects[key])


@pytest.fixture
def job(tmp_path, monkeypatch):
    source, proof, capture_sha = fixture_capture(tmp_path)
    index = native.prepare_index(
        source, capture_sha, tmp_path / "index.json", checkpoint=lambda: None
    )["index"]
    received = tmp_path / "received.native"
    native.materialize_part(source, capture_sha, index, 0, received, checkpoint=lambda: None)
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    ledger = Ledger(tmp_path / "ledger")
    objects = {}
    publisher, verifier = Store(objects), Store(objects)

    def transport(left, right):
        if left is right:
            raise ArchiveBlocked("Independent verifier required")
        assert left.objects is right.objects

    monkeypatch.setattr(scientific_registry_import, "_transport", transport)
    monkeypatch.setattr(scientific_registry_import, "_provider_proof", lambda *a: None)
    staged = parent.stage(
        proof,
        capture_sha,
        index,
        0,
        received,
        b"k" * 32,
        work / "stage",
        source_proof_sha256="a" * 64,
        checkpoint=lambda: None,
    )
    receipt = parent.publish_stage(staged["stage"], publisher, ledger, checkpoint=lambda: None)
    arguments = dict(
        identity=staged["candidate"],
        ledger=ledger,
        publisher=publisher,
        verifier=verifier,
        key=b"k" * 32,
        workdir=work,
        checkpoint=lambda: None,
        provider_proof_path=tmp_path / "proof",
        provider_proof_sha256="b" * 64,
        provider_terminal_path=tmp_path / "terminal",
        provider_terminal_sha256="c" * 64,
    )
    yield SimpleNamespace(
        args=arguments,
        receipt=receipt,
        objects=objects,
        work=work,
        stage=Path(staged["stage"]),
        native=received,
        proof=proof,
        index=index,
    )
    ledger.close()


def test_existing_pipeline_seals_private_operational_values_and_independent_ro_reads_all(job):
    consumed = []

    def consume(body, plaintext):
        consumed.append(Path(plaintext).read_bytes())
        assert body["science_compatibility_proven"] is False
        return {"synthetic_consumer": True}

    result = parent.readback(**job.args, consumer=consume)
    assert consumed == [job.native.read_bytes()]
    assert result["source_data_accesses"] == 0 and result["independent_readback"]
    assert result["quarantined"] and not result["archive_accepted"]
    assert len(job.args["verifier"].gets) == 3
    assert all(b"private-operational-error" not in body for body in job.objects.values())
    index_ref = json.loads((job.stage / "stage.json").read_bytes())["files"][0]
    metadata = list(parquet.read(job.stage / "0000.parquet", index_ref, checkpoint=lambda: None))[0]
    assert b"private-operational-error" not in json.dumps(metadata).encode()
    assert job.receipt["state"] == "published" and job.receipt["source"]["quarantined"]


@pytest.mark.parametrize("attack", ["manifest", "index", "cipher", "key", "same_verifier"])
def test_corrupt_archive_or_wrong_identity_never_reaches_sql_consumer(job, attack):
    called = []
    if attack == "key":
        job.args["key"] = b"x" * 32
    elif attack == "same_verifier":
        job.args["verifier"] = job.args["publisher"]
    else:
        ref = (
            job.receipt["manifest"]
            if attack == "manifest"
            else job.receipt["files"][0 if attack == "index" else 1]
        )
        job.objects[ref["key"]] = b"corrupt"
    with pytest.raises((ArchiveBlocked, ValueError)):
        parent.readback(**job.args, consumer=lambda *a: called.append(True))
    assert called == []


def test_missing_native_part_fails_without_any_source_fallback(job):
    del job.objects[job.receipt["files"][1]["key"]]
    with pytest.raises(KeyError):
        parent.readback(**job.args)


def test_source_capture_and_backup_bindings_are_immutable_before_sealing(job):
    bad = copy.deepcopy(job.proof)
    bad["schema_master_sha256"] = "f" * 64
    with pytest.raises(ArchiveBlocked, match="completion"):
        parent.stage(
            bad,
            job.index["capture_sha256"],
            job.index,
            0,
            job.native,
            b"k" * 32,
            job.work / "other",
            source_proof_sha256="a" * 64,
            checkpoint=lambda: None,
        )


@pytest.mark.parametrize("mismatch", [False, True])
def test_actual_science_parent_compatibility_is_a_separate_sql_gate(mismatch):
    calls = []
    db = SimpleNamespace(
        execute=lambda query: calls.append(str(query))
        or SimpleNamespace(scalar_one=lambda: mismatch)
    )
    args = dict(
        expected_science_capture_sha256="a" * 64,
        schema_master_sha256="b" * 64,
        expected_schema_master_sha256="b" * 64,
    )
    if mismatch:
        with pytest.raises(ArchiveBlocked, match="identity/calibration"):
            parent.science_compatibility(db, "a" * 64, **args)
    else:
        proof = parent.science_compatibility(db, "a" * 64, **args)
        assert proof["science_compatibility_proven"]
        assert not proof["same_snapshot_as_previous_science"]
        assert not proof["historical_assignment_inferred"] and not proof["archive_accepted"]
    assert "source_sha256" in calls[0] and "calibration_version" in calls[0]


@pytest.mark.parametrize("source", ["gateway", "direct"])
def test_faithful_native_restore_preserves_error_null_and_retry_without_defaults(
    tmp_path, monkeypatch, source
):
    """Explicit SQLite transport adapter; real target rows/PK/FK transaction."""
    from app.raw_archive import scientific_parts
    from app.raw_archive.scientific_capture import HEADER, TRAILER, digest_json
    from tests.raw_archive.test_scientific_application_restore import pack, unpack

    folder, proof, capture_sha = fixture_capture(tmp_path, source)
    index = native.prepare_index(
        folder, capture_sha, tmp_path / "index.json", checkpoint=lambda: None
    )["index"]
    engine = create_engine("sqlite://")
    tables = native.scope(source)["tables"]
    with engine.begin() as db:
        db.execute(text("PRAGMA foreign_keys=ON"))
        for position, table in enumerate(tables):
            columns = ",".join(
                '"' + c["name"] + '" BLOB' + (" NOT NULL" if c["not_null"] else "")
                for c in table["columns"]
            )
            pk = table["columns"][0]["name"]
            extra = ',PRIMARY KEY("' + pk + '")'
            if position:
                extra += (
                    ',FOREIGN KEY("'
                    + pk
                    + '") REFERENCES "'
                    + tables[0]["name"]
                    + '"("'
                    + tables[0]["columns"][0]["name"]
                    + '")'
                )
            db.execute(text('CREATE TABLE "' + table["name"] + '" (' + columns + extra + ")"))

    class Cursor:
        def __init__(self, db):
            self.db = db

        def mogrify(self, query, args):
            return query, args[0]

        def copy_expert(self, query, stream):
            if isinstance(query, tuple):
                sql, ids = query
                name = re.search('JOIN public."([a-z_]+)"', sql)[1]
                table = next(t for t in tables if t["name"] == name)
                pk = table["columns"][0]["name"]
                stream.write(HEADER)
                for identifier in ids:
                    key = (
                        uuid.UUID(identifier).bytes
                        if table["columns"][0]["type"] == "uuid"
                        else identifier.encode()
                    )
                    row = self.db.execute(
                        text('SELECT * FROM "' + name + '" WHERE "' + pk + '"=:id'), {"id": key}
                    ).first()
                    if row is not None:
                        stream.write(pack(row))
                stream.write(TRAILER)
            else:
                name = re.search('COPY public."([a-z_]+)"', query)[1]
                table = next(t for t in tables if t["name"] == name)
                data = b""
                while block := stream.read(8192):
                    data += block
                body = io.BytesIO(data[len(HEADER) :])
                while (row := scientific_parts._row(body, len(table["columns"]))) is not None:
                    values = unpack(row)
                    fields = ",".join('"' + c["name"] + '"' for c in table["columns"])
                    params = ",".join(":v" + str(i) for i in range(len(values)))
                    self.db.execute(
                        text('INSERT INTO "' + name + '" (' + fields + ") VALUES (" + params + ")"),
                        {"v" + str(i): v for i, v in enumerate(values)},
                    )

        def close(self):
            pass

    class DB:
        def __enter__(self):
            self.db = engine.connect()
            self.connection = SimpleNamespace(
                driver_connection=SimpleNamespace(cursor=lambda: Cursor(self.db))
            )
            return self

        def __exit__(self, *_):
            self.db.close()

        def begin(self):
            return self.db.begin()

        def execute(self, query, params=None):
            sql = str(query)
            if sql.startswith(("SET ", "LOCK ")):
                return None
            if "pg_try_advisory" in sql:
                return SimpleNamespace(scalar_one=lambda: True)
            return self.db.execute(text(sql.replace("public.", "")), params or {})

    monkeypatch.setattr(parent, "_target", lambda *a: None)
    monkeypatch.setattr(parent, "_portable_schema", lambda *a: None)
    target = SimpleNamespace(connect=DB)
    for position in range(len(index["parts"])):
        part = tmp_path / f"part-{position}"
        native.materialize_part(folder, capture_sha, index, position, part, checkpoint=lambda: None)
        body = {
            "capture": proof,
            "capture_sha256": capture_sha,
            "index": index,
            "index_sha256": digest_json(index),
            "position": position,
        }
        result = parent.restore_part(
            body, part, target, {}, "e" * 64, checkpoint=lambda: None, target_guard=lambda: None
        )
        assert result["all_captured_columns_preserved"] and not result["replayed"]
        assert parent.restore_part(
            body, part, target, {}, "e" * 64, checkpoint=lambda: None, target_guard=lambda: None
        )["replayed"]
    with engine.connect() as db:
        for table in tables:
            field = "feature_error" if table["name"] == "wav_file" else "error"
            values = list(
                db.execute(
                    text(
                        'SELECT "' + field + '" FROM "' + table["name"] + '" ORDER BY 1 NULLS LAST'
                    )
                ).scalars()
            )
            assert set(values) == {b"private-operational-error", None}
        if source == "direct":
            retry = db.execute(text("SELECT retry_at FROM direct_segment LIMIT 1")).scalar_one()
            assert struct.unpack("!d", retry)[0] == 1.5
    contract = {"masters_sha256": "e" * 64}
    monkeypatch.setattr(parent, "_master_anchor", lambda *a: {"master_epoch": 7})
    audits = []
    for position in range(len(index["parts"])):
        body = {
            "capture": proof,
            "capture_sha256": capture_sha,
            "index": index,
            "index_sha256": digest_json(index),
            "position": position,
        }
        audits.append(
            parent.restore_part(
                body,
                tmp_path / f"part-{position}",
                target,
                {},
                "e" * 64,
                checkpoint=lambda: None,
                target_guard=lambda: None,
                audit_only=True,
                master_contract=contract,
            )
        )
    complete = parent.complete_restore(
        proof,
        capture_sha,
        index,
        audits,
        target,
        {},
        "e" * 64,
        contract,
        checkpoint=lambda: None,
        target_guard=lambda: None,
    )
    assert (
        complete["faithful_parent_restore_verified"]
        and not complete["science_compatibility_proven"]
    )
    monkeypatch.setattr(parent, "_master_anchor", lambda *a: {"master_epoch": 8})
    with pytest.raises(ArchiveBlocked, match="epochs"):
        parent.complete_restore(
            proof,
            capture_sha,
            index,
            audits,
            target,
            {},
            "e" * 64,
            contract,
            checkpoint=lambda: None,
            target_guard=lambda: None,
        )


def test_parent_audit_requires_real_existing_master_baseline():
    with pytest.raises(ArchiveBlocked, match="master baseline contract"):
        parent._master_anchor(None, {}, None)


@pytest.mark.parametrize("failure", [None, "control", "aborted_sql"])
def test_existing_public_control_journals_visible_with_catalog_first_and_path_restored(
    monkeypatch, failure
):
    from app.tiered_archive import (
        codec,
        restore_baseline,
        restore_fence,
        restore_session,
        restore_universe,
    )

    plan = {
        "format": restore_session.FORMAT,
        "universe": {
            "version": 1,
            "tables": {"wav_file": "master", "visual_wav": "master", "wav_feature": "data"},
        },
        "environment": "production",
        "source": "gateway",
        "dataset": "wav_feature",
        "organization": "00000000-0000-0000-0000-000000000000",
        "schema_master_sha256": "a" * 64,
        "expected_pages": 1,
        "expected_rows": 2,
        "first_batch_sha256": "b" * 64,
    }
    contract = {"plan": plan, "plan_sha256": codec.digest(plan), "masters_sha256": "c" * 64}
    state = {"path": "pg_catalog", "failed": False}
    commands = []

    class DB:
        def execute(self, query):
            sql = str(query)
            commands.append(sql)
            if sql == "SHOW search_path":
                return SimpleNamespace(scalar_one=lambda: state["path"])
            assert sql in {
                "SET LOCAL search_path=pg_catalog,public",
                "SET LOCAL search_path=pg_catalog",
            }
            if failure == "aborted_sql" and state["failed"]:
                raise RuntimeError("transaction_already_aborted")
            state["path"] = sql.split("=", 1)[1]

    def bind(db, actual_plan):
        assert state["path"] == "pg_catalog,public"
        assert actual_plan == plan
        if failure:
            state["failed"] = True
            raise ArchiveBlocked("expected_control_failure")

    monkeypatch.setattr(restore_universe, "bind", bind)
    monkeypatch.setattr(restore_universe, "tables", lambda *args: ({}, {"wav_file": object()}))
    monkeypatch.setattr(restore_fence, "check", lambda *args: {"master_epoch": 7})
    monkeypatch.setattr(restore_baseline, "anchor", lambda *args: "c" * 64)
    if failure:
        with pytest.raises(ArchiveBlocked, match="expected_control_failure"):
            parent._master_anchor(
                DB(), {"kind": "gateway", "schema_master_sha256": "a" * 64}, contract
            )
    else:
        assert parent._master_anchor(
            DB(), {"kind": "gateway", "schema_master_sha256": "a" * 64}, contract
        ) == {"master_epoch": 7}
    assert commands == [
        "SHOW search_path",
        "SET LOCAL search_path=pg_catalog,public",
        "SET LOCAL search_path=pg_catalog",
    ]
    if failure != "aborted_sql":
        assert state["path"] == "pg_catalog"
