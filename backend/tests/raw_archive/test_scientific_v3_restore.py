"""Offline-only checks for native V3 row comparison and target fences."""

import struct
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.raw_archive import scientific_capture as capture
from app.raw_archive import scientific_restore as restore
from app.raw_archive.policy import ArchiveBlocked
from tests.raw_archive.test_scientific_v3 import row


def write(path, rows):
    Path(path).write_bytes(capture.HEADER + b"".join(rows) + capture.TRAILER)


def test_complete_native_rows_compared_by_pk_regardless_of_copy_order(tmp_path):
    columns = capture.roster()["wav_feature"]
    one, two = row(62, b"\x01" * 16), row(62, b"\x02" * 16)
    a, b = tmp_path / "a", tmp_path / "b"
    write(a, [one, two])
    write(b, [two, one])
    assert restore._rows(a, columns, 2, lambda: None) == restore._rows(b, columns, 2, lambda: None)
    different = bytearray(two)
    different[34:38] = struct.pack("!i", 0)
    write(b, [one, bytes(different)])
    assert restore._rows(a, columns, 2, lambda: None) != restore._rows(b, columns, 2, lambda: None)


def test_duplicate_pk_and_truncated_copy_are_blocked(tmp_path):
    columns = capture.roster()["wav_feature"]
    body = row(62, b"\x01" * 16)
    path = tmp_path / "part"
    write(path, [body, body])
    with pytest.raises(ArchiveBlocked, match="Duplicate"):
        restore._rows(path, columns, 2, lambda: None)
    path.write_bytes(capture.HEADER + body)
    with pytest.raises(ArchiveBlocked):
        restore._rows(path, columns, 1, lambda: None)


def test_restore_refuses_production_target_before_sql():
    engine = create_engine("postgresql+psycopg2://wrong:pass@127.0.0.1:5432/plantdb")
    target = {
        "database": "plantdb",
        "database_oid": 1,
        "role": "wrong",
        "role_oid": 2,
        "port": 5432,
        "server_address": "127.0.0.1/32",
        "postmaster_start": "x",
    }
    with pytest.raises(ArchiveBlocked, match="Dedicated loopback"):
        restore._target(None, engine, target)


@pytest.mark.parametrize("interrupted", [False, True])
def test_restore_controller_downloads_decrypts_copies_and_journals(tmp_path, interrupted):
    from types import SimpleNamespace
    from unittest.mock import patch

    from sqlalchemy.engine import make_url

    from tests.raw_archive.test_scientific_v3_archive import run, setup_job, whole

    args, _ = setup_job(tmp_path)
    run(args)
    args["position"] = 1
    run(args)
    root = whole(args)
    name = "science_v3_" + "a" * 32
    target = {
        "database": name,
        "database_oid": 123,
        "role": name,
        "role_oid": 124,
        "port": 15432,
        "server_address": "127.0.0.1/32",
        "postmaster_start": "fixed",
    }
    actual = {
        **target,
        "database_owner_oid": 124,
        "session_role": name,
        "unprivileged": True,
        "version": 150010,
        "in_recovery": False,
    }
    calls = []
    copied = []
    interrupt = [interrupted]

    class Cursor:
        def copy_expert(self, query, stream):
            calls.append(query)
            if "FROM STDIN" in query:
                while block := stream.read(8192):
                    copied.append(block)
            else:
                if interrupt[0]:
                    interrupt[0] = False
                    raise RuntimeError("simulated readback interruption")
                for block in copied or [capture.HEADER + capture.TRAILER]:
                    stream.write(block)

        def close(self):
            pass

    class Connection:
        def __init__(self):
            self.connection = SimpleNamespace(driver_connection=SimpleNamespace(cursor=Cursor))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def commit(self):
            calls.append("commit")

        def rollback(self):
            calls.append("rollback")

        def execute(self, query, *_params):
            calls.append(str(query))
            if "DROP TABLE" in str(query):
                assert (
                    args["ledger"]
                    .db.execute("SELECT COUNT(*) FROM science_v3_restore")
                    .fetchone()[0]
                    == 1
                )
            if "FROM pg_attribute" in str(query):
                columns = capture.roster()["wav_feature"]
                return SimpleNamespace(
                    mappings=lambda: [
                        {
                            key: column[key]
                            for key in ("name", "formatted_type", "not_null", "type_oid")
                        }
                        for column in columns
                    ]
                )
            if "FROM pg_class c WHERE" in str(query):
                return SimpleNamespace(one=lambda: ("r", "p", 124, True, True, True, 1, 1))
            if "SELECT count(*) FROM" in str(query):
                return SimpleNamespace(scalar_one=lambda: 0)
            return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: actual))

    engine = SimpleNamespace(
        url=make_url(f"postgresql+psycopg2://{name}:x@127.0.0.1:15432/{name}"), connect=Connection
    )
    with patch.object(restore.storage, "disk_reserve"):
        if interrupted:
            with pytest.raises(RuntimeError, match="simulated"):
                restore.restore_part(
                    root["reference"],
                    root["whole_sha256"],
                    0,
                    args["key"],
                    args["verifier"],
                    engine,
                    target,
                    args["workdir"],
                    args["ledger"],
                    checkpoint=lambda: None,
                    target_guard=lambda: None,
                )
            assert "rollback" in calls and not any("DROP TABLE" in item for item in calls)
        audit = restore.restore_part(
            root["reference"],
            root["whole_sha256"],
            0,
            args["key"],
            args["verifier"],
            engine,
            target,
            args["workdir"],
            args["ledger"],
            checkpoint=lambda: None,
            target_guard=lambda: None,
        )
    assert audit["native_row_multiset_equal"] and not audit["writer_allowed"]
    assert any("CREATE TABLE" in item and "character varying(20)" in item for item in calls)
    assert calls[-1] == "commit"
    assert next(i for i, item in enumerate(calls) if "DROP TABLE" in item) > next(
        i for i, item in enumerate(calls) if "COPY (SELECT" in item
    )
    assert args["ledger"].db.execute("SELECT COUNT(*) FROM science_v3_restore").fetchone()[0] == 1
