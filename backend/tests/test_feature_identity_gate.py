"""Physical local indexes and the real worker's pause-before-write contract."""

from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.services import feature_identity_gate as gate
from app.services import wav_feature_service as service
from tests.test_wav_features import _wav_bytes, _wav_record


def _index(columns=gate.IDENTITY):
    return {
        "unique": True,
        "primary": False,
        "valid": True,
        "ready": True,
        "live": True,
        "immediate": True,
        "exclusion": False,
        "attributes": 6,
        "key_attributes": 6,
        "no_expressions": True,
        "no_predicate": True,
        "same_collations": True,
        "method": "btree",
        "columns": list(columns),
        "not_null": [True] * 6,
        "default_ops": [True] * 6,
        "options": [0] * 6,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("valid", False),
        ("ready", False),
        ("live", False),
        ("immediate", False),
        ("exclusion", True),
        ("no_expressions", False),
        ("no_predicate", False),
        ("same_collations", False),
        ("method", "hash"),
        ("attributes", 7),
        ("key_attributes", 5),
        ("not_null", [True] * 5 + [False]),
        ("default_ops", [True] * 5 + [False]),
        ("options", [0] * 5 + [1]),
        ("columns", list(reversed(gate.IDENTITY))),
        ("columns", list(gate.IDENTITY[:4])),
        ("columns", list(gate.IDENTITY[:5]) + ["unrelated"]),
    ],
)
def test_pg_catalog_bad_physical_index_never_authorizes(field, value):
    row = _index()
    row[field] = value
    assert not gate._postgres_ready([row])


def test_pg_catalog_requires_six_and_no_old_or_unknown_unique():
    assert gate._postgres_ready([_index()])
    assert not gate._postgres_ready([])
    assert not gate._postgres_ready([_index()] * 65)
    old = _index(gate.IDENTITY[:4])
    assert not gate._postgres_ready([_index(), old])
    old["valid"] = False
    assert not gate._postgres_ready([_index(), old])
    assert not gate._postgres_ready([_index(), _index(("source_sha256",))])
    exclusion = _index()
    exclusion.update(unique=False, exclusion=True)
    assert not gate._postgres_ready([_index(), exclusion])


@pytest.mark.parametrize("held", [True, False])
def test_pg_locks_precede_actual_catalog_check_and_match_existing_migration(held):
    calls = []

    class Connection:
        dialect = SimpleNamespace(name="postgresql")

        def execute(self, sql, params=None):
            if params is not None:
                assert params == {"namespace": 1196246601, "identity": 1}
                calls.append("advisory")
                return SimpleNamespace(scalar_one=lambda: held)
            assert "pg_catalog.to_regclass('wav_feature_version')" in str(sql)
            calls.append("catalog")
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [_index()]))

        def exec_driver_sql(self, sql):
            assert sql == "LOCK TABLE ONLY public.wav_feature_version IN ACCESS SHARE MODE NOWAIT"
            calls.append("table_lock")

    db = SimpleNamespace(connection=lambda: Connection())
    if held:
        gate.require_feature_identity(db)
        assert calls == ["advisory", "table_lock", "catalog"]
    else:
        with pytest.raises(gate.FeatureIdentityWait):
            gate.require_feature_identity(db)
        assert calls == ["advisory"]


def test_catalog_failure_is_a_static_pause_without_exception_value_export():
    def fail():
        raise OperationalError("secret-dsn", {}, RuntimeError("secret-password"))

    with pytest.raises(gate.FeatureIdentityWait) as failure:
        gate.require_feature_identity(SimpleNamespace(connection=fail))
    assert str(failure.value) == "Archive paused: feature_identity_schema_wait"
    assert failure.value.__suppress_context__


@pytest.mark.parametrize(
    "shape,allowed",
    [
        ("old4", False),
        ("new6", True),
        ("old4+new6", False),
        ("partial6", False),
        ("expression6", False),
        ("no_index", False),
    ],
)
def test_actual_sqlite_physical_identity(shape, allowed):
    engine = create_engine("sqlite://")
    try:
        with Session(engine) as db:
            fields = ",".join(name + " TEXT NOT NULL" for name in gate.IDENTITY)
            db.execute(
                text("CREATE TABLE wav_feature_version (id INTEGER PRIMARY KEY," + fields + ")")
            )
            if shape in {"old4", "old4+new6"}:
                db.execute(
                    text(
                        "CREATE UNIQUE INDEX old4 ON wav_feature_version ("
                        + ",".join(gate.IDENTITY[:4])
                        + ")"
                    )
                )
            if shape in {"new6", "old4+new6", "partial6", "expression6"}:
                columns = list(gate.IDENTITY)
                if shape == "expression6":
                    columns[-1] = "lower(feature_checksum)"
                suffix = " WHERE source_sha256 != ''" if shape == "partial6" else ""
                db.execute(
                    text(
                        "CREATE UNIQUE INDEX new6 ON wav_feature_version ("
                        + ",".join(columns)
                        + ")"
                        + suffix
                    )
                )
            if allowed:
                gate.require_feature_identity(db)
                values = {name: name for name in gate.IDENTITY}
                sql = (
                    "INSERT INTO wav_feature_version ("
                    + ",".join(gate.IDENTITY)
                    + ") VALUES ("
                    + ",".join(":" + name for name in gate.IDENTITY)
                    + ")"
                )
                db.execute(text(sql), values)
                values["source_sha256"] = "different-source"
                values["feature_checksum"] = "different-result"
                db.execute(text(sql), values)
                assert (
                    db.execute(text("SELECT count(*) FROM wav_feature_version")).scalar_one() == 2
                )
            else:
                with pytest.raises(gate.FeatureIdentityWait):
                    gate.require_feature_identity(db)
    finally:
        engine.dispose()


def test_sqlite_temp_shadow_cannot_reuse_main_table_proof(db):
    db.execute(text("CREATE TEMP TABLE wav_feature_version (id INTEGER PRIMARY KEY)"))
    try:
        with pytest.raises(gate.FeatureIdentityWait):
            gate.require_feature_identity(db)
    finally:
        db.execute(text("DROP TABLE temp.wav_feature_version"))
        db.commit()


def test_catalog_probe_does_not_autoflush_pending_orm_changes(db):
    record = _record(db)
    _old4(db)
    record.feature_error = "pending-only"
    with pytest.raises(gate.FeatureIdentityWait):
        gate.require_feature_identity(db)
    db.rollback()
    db.refresh(record)
    assert record.feature_error is None


def _old4(db):
    db.execute(
        text(
            "CREATE UNIQUE INDEX blocked_old4 ON wav_feature_version ("
            + ",".join(gate.IDENTITY[:4])
            + ")"
        )
    )
    db.commit()


def _record(db):
    record = _wav_record(
        datetime(2026, 11, 1, tzinfo=UTC), _wav_bytes(np.arange(380, dtype=np.int16))
    )
    db.add(record)
    db.commit()
    return record


def test_old4_pauses_worker_before_candidate_claim_without_retry_or_feature_dml(db, monkeypatch):
    record = _record(db)
    before = (record.feature_status, record.feature_started_at, record.feature_attempts)
    _old4(db)
    writes = []

    def capture(_conn, _cursor, statement, *_rest):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        monkeypatch.setattr(
            service, "_next_candidate", lambda *_: pytest.fail("claimed before schema readiness")
        )
        assert service.process_pending_wav_features(db) == {"verified": 0, "failed": 0, "paused": 1}
        db.refresh(record)
        assert (record.feature_status, record.feature_started_at, record.feature_attempts) == before
        assert writes == []
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)


def test_old4_pauses_on_demand_extraction_before_download_or_intermediate_commit(db, monkeypatch):
    record = _record(db)
    _old4(db)
    monkeypatch.setattr(
        service.wav_service,
        "download_object",
        lambda *_: pytest.fail("download before schema readiness"),
    )
    monkeypatch.setattr(db, "commit", lambda: pytest.fail("intermediate commit while paused"))
    with pytest.raises(gate.FeatureIdentityWait):
        service.extract_and_verify_wav_features(db, record)
    with pytest.raises(gate.FeatureIdentityWait):
        service._extract_and_verify_wav_features(db, record)
    with pytest.raises(gate.FeatureIdentityWait):
        service._snapshot_feature_version(db, SimpleNamespace(verified_at=datetime.now(UTC)))


def test_snapshot_wait_precedes_orm_attribute_reads_or_autoflush(db):
    _old4(db)

    class ExpiredFeature:
        @property
        def verified_at(self):
            pytest.fail("ORM attribute read before physical schema readiness")

    with pytest.raises(gate.FeatureIdentityWait):
        service._snapshot_feature_version(db, ExpiredFeature())


def test_late_schema_pause_restores_only_own_claim_and_does_not_mark_failed(db, monkeypatch):
    record = _record(db)
    before = (
        record.feature_status,
        record.feature_started_at,
        record.feature_attempts,
        record.feature_error,
    )
    monkeypatch.setattr(service, "_next_candidate", lambda *_: record)

    def pause(*_):
        raise gate.FeatureIdentityWait()

    monkeypatch.setattr(service, "extract_and_verify_wav_features", pause)
    assert service.process_pending_wav_features(db) == {"verified": 0, "failed": 0, "paused": 1}
    db.refresh(record)
    assert (
        record.feature_status,
        record.feature_started_at,
        record.feature_attempts,
        record.feature_error,
    ) == before


def test_pause_cleanup_does_not_overwrite_another_claim_or_completed_status(db, monkeypatch):
    record = _record(db)
    monkeypatch.setattr(service, "_next_candidate", lambda *_: record)

    def other_writer_finished(*_):
        record.feature_status = "verified"
        record.feature_attempts = 0
        db.commit()
        raise gate.FeatureIdentityWait()

    monkeypatch.setattr(service, "extract_and_verify_wav_features", other_writer_finished)
    assert service.process_pending_wav_features(db)["paused"] == 1
    db.refresh(record)
    assert record.feature_status == "verified"
    assert record.feature_attempts == 0


def test_standalone_worker_reports_normal_paused_heartbeat(monkeypatch):
    from app.workers import wav_features as worker

    calls = []

    class StopCycle(Exception):
        pass

    monkeypatch.setattr(
        worker,
        "settings",
        SimpleNamespace(
            log_level="INFO",
            wav_feature_interval_minutes=1,
            wav_feature_extraction_enabled=True,
        ),
    )
    monkeypatch.setattr(worker, "setup_logging", lambda *_: None)
    monkeypatch.setattr(
        worker, "SessionLocal", lambda: SimpleNamespace(close=lambda: calls.append("closed"))
    )
    result = {"verified": 0, "failed": 0, "paused": 1}
    monkeypatch.setattr(worker, "process_pending_wav_features", lambda *_: result)
    monkeypatch.setattr(
        worker,
        "update_heartbeat",
        lambda _db, name, status, state: calls.append((name, status, state)),
    )

    def sleep(delay):
        assert delay == 60
        raise StopCycle()

    monkeypatch.setattr(worker.time, "sleep", sleep)
    with pytest.raises(StopCycle):
        worker.main()
    assert calls == [("wav_features", "paused", result), "closed"]
