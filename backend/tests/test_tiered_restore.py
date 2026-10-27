"""Archive readback and real SQL rollback; SQLite does not prove PostgreSQL locks."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event, text

from app.raw_archive.policy import ArchiveBlocked, checksum
from app.tiered_archive import parquet
from app.tiered_archive.pipeline import candidate_json
from app.tiered_archive.policy import Candidate
from app.tiered_archive.recovery import restore_database, restore_url
from tests.test_tiered_archive import AT, ORG, SENSOR

URL = "postgresql+psycopg2://restore@127.0.0.1:35432/greenmind_restore_test"


@pytest.fixture
def target(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "isolated.sqlite"))

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def postgres_directives(conn, cursor, statement, parameters, context, many):
        if statement.startswith(("SET LOCAL ", "LOCK TABLE ")):
            return "SELECT 1", ()
        return statement, parameters

    with engine.begin() as db:
        db.execute(text("CREATE TABLE ingest_log(measurement_id TEXT PRIMARY KEY, payload BLOB)"))
        db.execute(text("CREATE TABLE direct_chunk(id INTEGER PRIMARY KEY, payload BLOB)"))
        db.execute(
            text(
                "CREATE TABLE direct_piece(chunk_id INTEGER, segment_id TEXT, value TEXT, PRIMARY KEY(chunk_id,segment_id))"
            )
        )
        db.execute(
            text(
                "CREATE TABLE direct_chunk_payload_ref(chunk_id INTEGER PRIMARY KEY, object_key TEXT)"
            )
        )
    monkeypatch.setattr("sqlalchemy.create_engine", lambda *args, **kwargs: engine)
    yield engine
    engine.dispose()


def bundle(tmp_path, groups, dataset="ingest_log"):
    folder = tmp_path / "bundle"
    folder.mkdir()
    references = []
    for number, group in enumerate(groups):
        rows = [
            {
                "identity": f"{index:04}",
                "organization": ORG,
                "sensor": SENSOR,
                "time_us": number * 1000 + index,
                "body": body,
            }
            for index, body in enumerate(group)
        ]
        references.append(
            dict(parquet.write(folder / f"{number:04}.parquet", rows), format="parquet")
        )
    candidate = Candidate(
        "staging",
        "gateway" if dataset == "ingest_log" else "direct",
        dataset,
        ORG,
        AT,
        AT + timedelta(days=1),
        references[0]["content_sha256"],
        sum(len(group) for group in groups),
        "logs" if dataset == "ingest_log" else "direct_payload",
        AT,
    )
    manifest = {
        "candidate": candidate_json(candidate),
        "identity": candidate.identity,
        "files": references,
    }
    path = folder / "manifest.json"
    path.write_text(json.dumps(manifest))
    return folder, checksum(path)


def log(identity):
    return {"row": {"measurement_id": identity, "payload": b"\x00\xff"}, "context": {}}


def chunk():
    return {
        "row": {"id": 1, "payload": b"\x00\xff"},
        "context": {
            "pieces": [{"chunk_id": 1, "segment_id": "segment", "value": "original"}],
            "payload_reference": {"chunk_id": 1, "object_key": "original-object"},
        },
    }


def counts(engine):
    with engine.connect() as db:
        return [
            db.execute(text("SELECT count(*) FROM " + table)).scalar_one()
            for table in ("ingest_log", "direct_chunk", "direct_piece", "direct_chunk_payload_ref")
        ]


@pytest.mark.parametrize(
    "url",
    [
        URL + "?host=production",
        URL + "?hostaddr=203.0.113.1",
        URL + "?service=production",
        URL + "?dbname=production",
        URL + "?options=x",
        URL.replace("127.0.0.1", "database"),
        URL.replace("greenmind_restore_test", "plantdb_staging"),
        URL.replace("postgresql+psycopg2", "mysql"),
        URL.replace("restore@", ""),
    ],
)
def test_target_override_blocked_before_connection(url, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Must not construct an engine for an unsafe restore URL")

    monkeypatch.setattr("sqlalchemy.create_engine", forbidden)
    with pytest.raises(ArchiveBlocked, match="dedicated local"):
        restore_database(tmp_path, url, "0" * 64, checkpoint=lambda: None)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
def test_explicit_loopback_target(host):
    assert restore_url(URL.replace("127.0.0.1", host)).database == "greenmind_restore_test"


def test_all_parts_read_back_and_binary_values_preserved(target, tmp_path):
    folder, sha = bundle(tmp_path, [[log("first")], [log("second")]])
    result = restore_database(folder, URL, sha, checkpoint=lambda: None)
    assert result == {"restored_rows": 2, "typed_values_verified": True, "source_accesses": 0}
    with target.connect() as db:
        assert (
            db.execute(text("SELECT payload FROM ingest_log ORDER BY measurement_id"))
            .scalars()
            .all()
            == [b"\x00\xff"] * 2
        )


def test_corruption_in_later_part_rolls_back_first_part(target, tmp_path):
    folder, sha = bundle(tmp_path, [[log("first")], [log("second")]])
    (folder / "0001.parquet").write_bytes(b"invalid")
    with pytest.raises(ArchiveBlocked, match="checksum"):
        restore_database(folder, URL, sha, checkpoint=lambda: None)
    assert counts(target) == [0, 0, 0, 0]


def test_later_part_database_trigger_mutation_rolls_back(target, tmp_path):
    folder, sha = bundle(tmp_path, [[log("first")], [log("second")]])
    with target.begin() as db:
        db.execute(
            text(
                "CREATE TRIGGER corrupt AFTER INSERT ON ingest_log WHEN NEW.measurement_id='second' BEGIN UPDATE ingest_log SET payload=x'03' WHERE measurement_id=NEW.measurement_id; END"
            )
        )
    with pytest.raises(ArchiveBlocked, match="values differ"):
        restore_database(folder, URL, sha, checkpoint=lambda: None)
    assert counts(target) == [0, 0, 0, 0]


@pytest.mark.parametrize(
    "child,field", [("direct_piece", "value"), ("direct_chunk_payload_ref", "object_key")]
)
def test_child_trigger_mutation_rolls_back_every_insert(target, tmp_path, child, field):
    folder, sha = bundle(tmp_path, [[chunk()]], "direct_chunk")
    with target.begin() as db:
        db.execute(
            text(
                f"CREATE TRIGGER corrupt AFTER INSERT ON {child} BEGIN UPDATE {child} SET {field}='changed' WHERE chunk_id=NEW.chunk_id; END"
            )
        )
    with pytest.raises(ArchiveBlocked, match="dependencies differ"):
        restore_database(folder, URL, sha, checkpoint=lambda: None)
    assert counts(target) == [0, 0, 0, 0]


def test_direct_children_are_fully_restored(target, tmp_path):
    folder, sha = bundle(tmp_path, [[chunk()]], "direct_chunk")
    assert restore_database(folder, URL, sha, checkpoint=lambda: None)["typed_values_verified"]
    assert counts(target) == [0, 1, 1, 1]


def test_foreign_direct_child_blocked_and_rolled_back(target, tmp_path):
    body = chunk()
    body["context"]["pieces"][0]["chunk_id"] = 99
    folder, sha = bundle(tmp_path, [[body]], "direct_chunk")
    with pytest.raises(ArchiveBlocked, match="another chunk"):
        restore_database(folder, URL, sha, checkpoint=lambda: None)
    assert counts(target) == [0, 0, 0, 0]


def test_preexisting_child_remains_unchanged(target, tmp_path):
    folder, sha = bundle(tmp_path, [[chunk()]], "direct_chunk")
    with target.begin() as db:
        db.execute(text("INSERT INTO direct_piece VALUES(99,'existing','keep')"))
    with pytest.raises(ArchiveBlocked, match="populated"):
        restore_database(folder, URL, sha, checkpoint=lambda: None)
    assert counts(target) == [0, 0, 1, 0]


def test_extra_child_added_by_trigger_rolls_back(target, tmp_path):
    folder, sha = bundle(tmp_path, [[chunk()]], "direct_chunk")
    with target.begin() as db:
        db.execute(
            text(
                "CREATE TRIGGER extra AFTER INSERT ON direct_piece WHEN NEW.chunk_id=1 BEGIN INSERT INTO direct_piece VALUES(99,'extra','bad'); END"
            )
        )
    with pytest.raises(ArchiveBlocked, match="dependency count"):
        restore_database(folder, URL, sha, checkpoint=lambda: None)
    assert counts(target) == [0, 0, 0, 0]


def test_changed_candidate_identity_blocks(target, tmp_path):
    folder, _ = bundle(tmp_path, [[log("one")]])
    path = folder / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["identity"] = "0" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises(ArchiveBlocked, match="source identity"):
        restore_database(folder, URL, checksum(path), checkpoint=lambda: None)
    assert counts(target) == [0, 0, 0, 0]


def test_cli_restore_report_with_file_count_is_not_a_receipt(monkeypatch, capsys):
    from contextlib import contextmanager

    from app.tiered_archive import __main__ as cli

    @contextmanager
    def worker():
        yield None, None, lambda: None

    report = {"identity": "synthetic", "rows": 1, "files": 2, "source_accesses": 0}
    monkeypatch.setattr(cli.runtime, "worker", worker)
    monkeypatch.setattr("app.tiered_archive.recovery.restore", lambda *args, **kwargs: report)
    monkeypatch.setattr(
        "sys.argv", ["archive", "restore", "--identity", "synthetic", "--output", "/unused"]
    )
    cli.main()
    assert json.loads(capsys.readouterr().out) == report
