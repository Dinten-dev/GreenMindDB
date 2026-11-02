"""Offline contract/transaction tests; the explicit SQLite adapter is test-only.

It executes real PK, unique and FK constraints on application tables while only
adapting PostgreSQL COPY transport. Native PG15 execution remains a Main task.
"""

import hashlib
import io
import re
import struct
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import (
    Column,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    text,
)
from sqlalchemy.exc import IntegrityError

from app.raw_archive import scientific_capture as capture
from app.raw_archive import scientific_parts as parts
from app.raw_archive import scientific_restore as restore
from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import (
    codec,
    restore_baseline,
    restore_fence,
    restore_session,
    restore_universe,
)


def native_row(table, number, parent=None):
    cells = []
    for c in capture.roster()[table]:
        name, kind = c["name"], c["formatted_type"]
        if not c["not_null"]:
            value = None
        elif kind == "uuid":
            value = uuid.UUID(int=(number if name == "id" else parent or number)).bytes
        elif kind == "integer":
            value = struct.pack("!i", 1)
        elif kind in ("bigint", "timestamp with time zone"):
            value = struct.pack("!q", 1)
        elif kind == "double precision":
            value = struct.pack("!d", 1)
        elif kind == "boolean":
            value = b"\x01"
        elif kind == "json":
            value = b"{}"
        else:
            value = b"version"
        cells.append(value)
    return pack(cells)


def pack(cells):
    return struct.pack("!h", len(cells)) + b"".join(
        struct.pack("!i", -1 if v is None else len(v)) + (v or b"") for v in cells
    )


def unpack(row):
    stream = io.BytesIO(row)
    count = struct.unpack("!h", stream.read(2))[0]
    result = []
    for _ in range(count):
        size = struct.unpack("!i", stream.read(4))[0]
        result.append(None if size < 0 else stream.read(size))
    return result


def fixture_whole(rows):
    index, refs, files, bodies = [], [], [], []
    for table in restore._SCIENCE_TABLES:
        all_rows = []
        for sequence, group in enumerate(rows[table]):
            body = capture.HEADER + b"".join(group) + capture.TRAILER
            part = dict(
                table=table,
                sequence=sequence,
                first_row=len(all_rows),
                rows=len(group),
                bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
            )
            index.append(part)
            refs.append(dict(sha256=hashlib.sha256(b"encrypted" + body).hexdigest()))
            bodies.append(body)
            all_rows.extend(group)
        body = capture.HEADER + b"".join(all_rows) + capture.TRAILER
        files.append(
            dict(
                table=table,
                columns=capture.roster()[table],
                rows=len(all_rows),
                bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
            )
        )
    return {
        "capture": {"files": files},
        "index": {"parts": index},
        "references": refs,
        "destination": "independent-ro",
    }, bodies


def contract(whole):
    tables = dict.fromkeys(("wav_file", "organization", "zone", "gateway", "sensor"), "master")
    tables.update(dict.fromkeys(restore._SCIENCE_TABLES, "data"))
    plan = dict(
        format=restore_session.FORMAT,
        universe={"version": 1, "tables": tables},
        environment="production",
        source="gateway",
        dataset="wav_feature",
        organization="00000000-0000-0000-0000-000000000000",
        schema_master_sha256="b" * 64,
        expected_pages=1,
        expected_rows=1,
        first_batch_sha256="c" * 64,
    )
    return dict(
        format=restore.APPLICATION_FORMAT,
        marker_sha256="d" * 64,
        plan=plan,
        plan_sha256=codec.digest(plan),
        masters_sha256="e" * 64,
        expected_rows=sum(f["rows"] for f in whole["capture"]["files"]),
        expected_parts=len(whole["index"]["parts"]),
        sessions={},
    )


@pytest.fixture
def target(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as db:
        db.execute(text("PRAGMA foreign_keys=ON"))
        db.execute(text("CREATE TABLE wav_file(id BLOB PRIMARY KEY)"))
        for table in restore._SCIENCE_TABLES:
            columns = capture.roster()[table]
            definitions = [
                f'"{c["name"]}" BLOB' + (" NOT NULL" if c["not_null"] else "") for c in columns
            ]
            definitions += ["PRIMARY KEY(id)", "FOREIGN KEY(wav_file_id) REFERENCES wav_file(id)"]
            definitions += (
                ["UNIQUE(wav_file_id)", "UNIQUE(flac_s3_key)", "UNIQUE(anomaly_s3_key)"]
                if table == "wav_feature"
                else ["UNIQUE(wav_file_id,extractor_version,calibration_version,parameter_hash)"]
            )
            db.execute(text('CREATE TABLE "' + table + '" (' + ",".join(definitions) + ")"))
        for i in range(1, 5):
            db.execute(text("INSERT INTO wav_file VALUES (:id)"), {"id": uuid.UUID(int=i).bytes})
        db.execute(
            text(
                "CREATE TABLE greenmind_restore_session(id TEXT,plan_sha256 TEXT,"
                "dataset TEXT,target_sha256 TEXT,state TEXT,rows INT,audit_rows INT,"
                "pages INT,audit_pages INT)"
            )
        )
    metadata = MetaData()
    state = Table(
        restore._JOURNALS[0],
        metadata,
        Column("id", Integer, primary_key=True),
        Column("binding", String(64), nullable=False),
        Column("audit", Text),
    )
    progress = Table(
        restore._JOURNALS[1],
        metadata,
        Column("position", Integer, primary_key=True),
        Column("binding", String(64), nullable=False),
        Column("keys", LargeBinary, nullable=False),
    )
    metadata.create_all(engine)
    queries = []

    class Cursor:
        def __init__(self, db):
            self.db = db

        def mogrify(self, query, args):
            return query, args[0]

        def copy_expert(self, query, stream):
            queries.append(query)
            if isinstance(query, tuple):
                query, ids = query
                table = re.search('JOIN public."([a-z_]+)"', query)[1]
                stream.write(capture.HEADER)
                for identifier in ids:
                    row = self.db.execute(
                        text('SELECT * FROM "' + table + '" WHERE id=:id'),
                        {"id": uuid.UUID(identifier).bytes},
                    ).first()
                    if row is not None:
                        stream.write(pack(tuple(row)))
                stream.write(capture.TRAILER)
            else:
                table = re.search('COPY public."([a-z_]+)"', query)[1]
                data = b""
                while block := stream.read(8192):
                    data += block
                source = io.BytesIO(data[len(capture.HEADER) :])
                columns = capture.roster()[table]
                while (row := parts._row(source, len(columns))) is not None:
                    names = ",".join('"' + c["name"] + '"' for c in columns)
                    values = ",".join(":v" + str(i) for i in range(len(columns)))
                    self.db.execute(
                        text('INSERT INTO "' + table + '" (' + names + ") VALUES (" + values + ")"),
                        {"v" + str(i): v for i, v in enumerate(unpack(row))},
                    )

        def close(self):
            pass

    class DB:
        def __getattr__(self, name):
            return getattr(self.db, name)

        def __enter__(self):
            self.db = engine.connect()
            self.connection = SimpleNamespace(
                driver_connection=SimpleNamespace(cursor=lambda: Cursor(self.db))
            )
            return self

        def __exit__(self, *args):
            self.db.close()

        def begin(self):
            return self.db.begin()

        def execute(self, query, *args):
            if str(query).startswith("SET LOCAL"):
                return None
            if "pg_try_advisory" in str(query):
                return SimpleNamespace(scalar_one=lambda: True)
            if isinstance(query, type(text(""))):
                query = text(str(query).replace("public.", ""))
            return self.db.execute(query, *args)

    snapshot = {"phase": "loading", "audit_epoch": None}
    monkeypatch.setattr(restore, "_application_identity", lambda *a: None)
    monkeypatch.setattr(restore, "_application_journals", lambda *a: (state, progress))
    monkeypatch.setattr(restore, "_application_context", lambda *a, **kw: snapshot.copy())
    monkeypatch.setattr(restore_fence, "accept", lambda *a: None)
    monkeypatch.setattr(restore_fence, "check", lambda *a: snapshot.copy())
    monkeypatch.setattr(restore_fence, "freeze", lambda *a: {"phase": "auditing", "audit_epoch": 1})
    whole, bodies = fixture_whole(
        {
            "wav_feature": [[native_row("wav_feature", 1)], [native_row("wav_feature", 2)]],
            "wav_feature_version": [[]],
        }
    )
    target_pin = {"database": "gm_native_restore_" + "a" * 12 + "_gateway"}
    args = dict(
        db_engine=SimpleNamespace(connect=DB),
        target=target_pin,
        contract=contract(whole),
        whole=whole,
        whole_sha256="f" * 64,
        output=tmp_path / "output",
        checkpoint=lambda: None,
        target_guard=lambda: None,
    )

    def run(position, *, final=False):
        path = tmp_path / "input"
        path.write_bytes(bodies[position])
        part = whole["index"]["parts"][position]
        original = restore._rows(path, capture.roster()[part["table"]], part["rows"], lambda: None)
        return restore._application_part(
            **args,
            position=position,
            reference=whole["references"][position],
            native=path,
            original=original,
            finalize=final,
        )

    return SimpleNamespace(
        run=run,
        args=args,
        engine=engine,
        whole=whole,
        bodies=bodies,
        queries=queries,
        progress=progress,
    )


def test_actual_tables_persist_transactional_parts_replay_and_full_native_digest(target):
    assert not target.run(0)["full_restore_verified"]
    assert target.run(0)["replayed"]
    target.run(1)
    target.run(2)
    proof = target.run(0, final=True)
    assert proof["full_restore_verified"] and proof["rows"] == 2
    assert proof["science_parent_fk_closure_proven"]
    assert not proof["archive_accepted"] and not proof["full_production_restore_accepted"]
    assert (
        proof["tables"]["wav_feature"]["native_sha256"]
        == target.whole["capture"]["files"][0]["sha256"]
    )
    assert any(isinstance(q, str) and 'COPY public."wav_feature"' in q for q in target.queries)
    assert not any("gm_science_v3_" in str(q) or "DROP TABLE" in str(q) for q in target.queries)
    with target.engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM wav_feature")).scalar_one() == 2
        assert db.execute(
            text("SELECT audit FROM greenmind_science_restore")
        ).scalar_one() == restore.v3._canonical(proof)


def test_missing_part_and_cross_part_primary_key_reject_atomically(target):
    with pytest.raises(ArchiveBlocked, match="Missing preceding"):
        target.run(1)
    target.run(0)
    target.bodies[1] = target.bodies[0]
    target.whole["index"]["parts"][1]["sha256"] = hashlib.sha256(target.bodies[1]).hexdigest()
    with pytest.raises(IntegrityError, match="UNIQUE"):
        target.run(1)
    with target.engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM wav_feature")).scalar_one() == 1
        assert (
            db.execute(text("SELECT count(*) FROM greenmind_science_restore_part")).scalar_one()
            == 1
        )


def test_actual_fk_and_cross_part_unique_are_enforced(target):
    with target.engine.begin() as db:
        db.execute(text("DELETE FROM wav_file WHERE id=:id"), {"id": uuid.UUID(int=1).bytes})
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        target.run(0)
    with target.engine.begin() as db:
        db.execute(text("INSERT INTO wav_file VALUES (:id)"), {"id": uuid.UUID(int=1).bytes})
    target.run(0)
    row = native_row("wav_feature", 2, parent=1)
    target.bodies[1] = capture.HEADER + row + capture.TRAILER
    target.whole["index"]["parts"][1]["sha256"] = hashlib.sha256(target.bodies[1]).hexdigest()
    with pytest.raises(IntegrityError, match="UNIQUE"):
        target.run(1)


@pytest.mark.parametrize("mutation", ["changed_value", "extra_row", "cipher", "target", "roster"])
def test_replay_rejects_changed_values_counts_cipher_target_and_roster(target, mutation):
    target.run(0)
    if mutation == "changed_value":
        with target.engine.begin() as db:
            db.execute(text("UPDATE wav_feature SET sensor_mac=:value"), {"value": b"changed"})
    elif mutation == "extra_row":
        with target.engine.begin() as db:
            db.execute(target.progress.delete())
    elif mutation == "cipher":
        target.whole["references"][0]["sha256"] = "0" * 64
    elif mutation == "target":
        target.args["target"]["database"] += "_changed"
    else:
        target.args["contract"]["sessions"]["1" * 64] = "2" * 64
    with pytest.raises(ArchiveBlocked):
        target.run(0)


def test_final_requires_every_part_then_detects_prior_part_corruption(target):
    target.run(0)
    with pytest.raises(ArchiveBlocked, match="Every science part"):
        target.run(0, final=True)
    target.run(1)
    target.run(2)
    with target.engine.begin() as db:
        db.execute(
            text("UPDATE wav_feature SET sensor_mac=:value WHERE id=:id"),
            {"value": b"changed", "id": uuid.UUID(int=2).bytes},
        )
    with pytest.raises(ArchiveBlocked, match="Final actual"):
        target.run(0, final=True)


def test_final_rejects_forged_capture_digest_and_unverified_universe_sessions(target):
    for i in range(3):
        target.run(i)
    original = target.whole["capture"]["files"][0]["sha256"]
    target.whole["capture"]["files"][0]["sha256"] = "0" * 64
    with pytest.raises(ArchiveBlocked, match="Full actual table"):
        target.run(0, final=True)
    target.whole["capture"]["files"][0]["sha256"] = original
    with target.engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO greenmind_restore_session VALUES "
                "('unexpected','bad','visual_wave','bad','open',1,0,1,0)"
            )
        )
    with pytest.raises(ArchiveBlocked, match="universe sessions"):
        target.run(0, final=True)


@pytest.mark.parametrize("missing", ["wav_file", "sensor", "gateway", "zone", "organization"])
def test_contract_requires_actual_parent_master_universe(missing):
    whole, _ = fixture_whole({"wav_feature": [[]], "wav_feature_version": [[]]})
    value = contract(whole)
    del value["plan"]["universe"]["tables"][missing]
    value["plan_sha256"] = codec.digest(value["plan"])
    with pytest.raises(ArchiveBlocked, match="parent and master"):
        restore._application_contract(value, whole, "f" * 64, {})


def test_context_requires_current_master_baseline(monkeypatch):
    whole, _ = fixture_whole({"wav_feature": [[]], "wav_feature_version": [[]]})
    value = contract(whole)
    monkeypatch.setattr(restore_universe, "bind", lambda *a: None)
    monkeypatch.setattr(restore_universe, "tables", lambda *a: ({}, {}))
    monkeypatch.setattr(restore_fence, "check", lambda *a: {"phase": "loading"})
    monkeypatch.setattr(restore_baseline, "anchor", lambda *a: "0" * 64)
    with pytest.raises(ArchiveBlocked, match="baseline"):
        restore._application_context(None, value, {}, loading=True)


def test_target_requires_loopback_port_and_existing_restore_namespace():
    from sqlalchemy.engine import make_url

    pin = dict(
        database="gm_native_restore_" + "a" * 12 + "_gateway",
        database_oid=1,
        role="isolated",
        role_oid=2,
        port=19442,
        server_address="127.0.0.1/32",
        postmaster_start="pinned",
    )
    actual = dict(
        pin,
        database_owner_oid=2,
        session_role="isolated",
        unprivileged=True,
        version=150010,
        in_recovery=False,
    )
    db = SimpleNamespace(
        execute=lambda *a: SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: actual))
    )
    engine = SimpleNamespace(
        url=make_url(f"postgresql+psycopg2://isolated:pw@127.0.0.1:19442/{pin['database']}")
    )
    assert restore._target(db, engine, pin, application=True) == actual
    changed = dict(pin, postmaster_start="changed")
    with pytest.raises(ArchiveBlocked, match="identity changed"):
        restore._target(db, engine, changed, application=True)
    changed = dict(pin, port=5432)
    with pytest.raises(ArchiveBlocked, match="Dedicated loopback"):
        restore._target(None, engine, changed, application=True)


def test_application_names_are_exact_universe_exemptions():
    assert set(restore._JOURNALS) <= restore_universe.OWN_TABLES
    assert "greenmind_science_restore_other" not in restore_universe.OWN_TABLES


def test_exact_owned_journal_schema_can_be_reopened_without_creating_tables():
    class DB:
        def execute(self, query, params=None):
            sql = str(query)
            name = params["name"].split(".")[1] if params else ""
            pk = "position" if name.endswith("_part") else "id"
            if "IS NOT NULL" in sql:
                return SimpleNamespace(scalar_one=lambda: True)
            if "FROM pg_class" in sql:
                marker = restore.APPLICATION_FORMAT + ":" + "a" * 64 + ":" + name
                return SimpleNamespace(
                    one_or_none=lambda: (
                        "r",
                        "p",
                        2,
                        True,
                        marker,
                        True,
                        True,
                        1,
                        [pk],
                        True,
                    )
                )
            if "FROM pg_attribute" in sql:
                columns = [(pk, "integer", True), ("binding", "character varying(64)", True)]
                columns.append(
                    ("keys", "bytea", True) if pk == "position" else ("audit", "text", False)
                )
                return [(*row, "", "", None) for row in columns]
            assert sql.startswith("LOCK TABLE")

    restore._application_journals(DB(), {"role_oid": 2}, "a" * 64, create=False)


@pytest.mark.parametrize(
    "fault", [None, "missing_fk", "missing_unique", "disabled_fk", "column", "index", "owner"]
)
def test_actual_schema_rejects_staging_and_weakened_constraints(fault):
    class DB:
        def execute(self, query, params):
            name = params["relation"].split(".")[1]
            sql = str(query)
            if "FROM pg_attribute" in sql:
                keys = (
                    "name",
                    "formatted_type",
                    "not_null",
                    "type_oid",
                    "identity_kind",
                    "generated_kind",
                    "default_expression",
                )
                rows = [
                    {k: c[k] for k in keys}
                    for c in restore.SOURCE_SCOPE["columns"]
                    if c["table_name"] == name
                ]
                if fault == "column":
                    rows.pop()
            elif "FROM pg_class c WHERE" in sql:
                return SimpleNamespace(
                    one_or_none=lambda: ("r", "p", 999 if fault == "owner" else 2, True, True)
                )
            elif "FROM pg_constraint k WHERE" in sql:
                keys = ("name", "type", "definition", "deferrable", "deferred", "validated")
                rows = [
                    {k: c[k] for k in keys}
                    for c in restore.SOURCE_SCOPE["constraints"]
                    if c["table_name"] == name
                ]
                if fault in {"missing_fk", "missing_unique"}:
                    rows = [r for r in rows if r["type"] != ("f" if fault == "missing_fk" else "u")]
            elif "FROM pg_index" in sql:
                keys = ("name", "definition", "primary_index", "unique_index", "ready", "valid")
                rows = [
                    {k: c[k] for k in keys}
                    for c in restore.SOURCE_SCOPE["indexes"]
                    if c["table_name"] == name
                ]
                if fault == "index":
                    rows.pop()
            else:
                return SimpleNamespace(scalar_one=lambda: fault == "disabled_fk")
            return SimpleNamespace(
                mappings=lambda: sorted(rows, key=lambda r: r["name"])
                if "FROM pg_attribute" not in sql
                else rows
            )

    if fault is None:
        restore._application_schema(DB(), {"role_oid": 2})
    else:
        with pytest.raises(ArchiveBlocked):
            restore._application_schema(DB(), {"role_oid": 2})


@pytest.mark.parametrize("fault", ["schema", "owner", "marker", "trigger", "pk", "deferred"])
def test_reserved_journal_names_cannot_adopt_foreign_tables(fault):
    binding = "a" * 64
    marker = restore.APPLICATION_FORMAT + ":" + binding + ":" + restore._JOURNALS[0]

    class DB:
        def execute(self, query, *_):
            sql = str(query)
            if "IS NOT NULL" in sql:
                return SimpleNamespace(scalar_one=lambda: True)
            if "FROM pg_class" in sql:
                value = (
                    "r",
                    "p",
                    99 if fault == "owner" else 2,
                    True,
                    "foreign" if fault == "marker" else marker,
                    fault != "trigger",
                    True,
                    1,
                    ["wrong_id"] if fault == "pk" else ["id"],
                    fault != "deferred",
                )
                return SimpleNamespace(one_or_none=lambda: value)
            if "FROM pg_attribute" in sql:
                return [
                    ("id", "text" if fault == "schema" else "integer", True),
                    ("binding", "character varying(64)", True),
                ]
            raise AssertionError("Unexpected SQL")

    with pytest.raises(ArchiveBlocked, match="Reserved science journal"):
        restore._application_journals(DB(), {"role_oid": 2}, binding)


@pytest.mark.parametrize("partial", [True, False])
def test_universe_exemption_requires_complete_owned_marker(partial):
    class DB:
        def execute(self, query, params=None):
            if "IS NOT NULL" in str(query):
                present = not partial or params["name"].endswith("_part")
                return SimpleNamespace(scalar_one=lambda: present)
            return SimpleNamespace(one=lambda: ("plantdb", 5432, 150010, 2, 2, False))

    with pytest.raises(ArchiveBlocked):
        restore.validate_application_journals(DB())


def test_encrypted_application_readback_has_no_source_fallback(tmp_path, monkeypatch):
    from tests.raw_archive.test_scientific_v3_archive import run, setup_job, whole

    args, objects = setup_job(tmp_path)
    run(args)
    args["position"] = 1
    run(args)
    root = whole(args)
    called = []
    monkeypatch.setattr(restore.storage, "disk_reserve", lambda *a: None)
    monkeypatch.setattr(
        restore, "_application_part", lambda **kwargs: called.append(kwargs) or {"actual": True}
    )
    result = restore.restore_part(
        root["reference"],
        root["whole_sha256"],
        0,
        args["key"],
        args["verifier"],
        None,
        {},
        args["workdir"],
        args["ledger"],
        checkpoint=lambda: None,
        target_guard=lambda: None,
        application={"test": True},
    )
    assert result == {"actual": True} and len(called) == 1
    objects[root["reference"]["key"]] = b"corrupt"
    with pytest.raises(ArchiveBlocked):
        restore.restore_part(
            root["reference"],
            root["whole_sha256"],
            0,
            args["key"],
            args["verifier"],
            None,
            {},
            args["workdir"],
            args["ledger"],
            checkpoint=lambda: None,
            target_guard=lambda: None,
            application={"test": True},
        )
    assert len(called) == 1
