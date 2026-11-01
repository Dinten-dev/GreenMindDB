"""Known bounded config and explicit headv3; synthetic PG metadata only."""

import os
from copy import deepcopy

import pytest
from sqlalchemy import JSON, Column, MetaData, Table
from sqlalchemy.dialects import postgresql

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import restore_inputs as inputs
from app.tiered_archive import restore_origin_capability as cap
from app.tiered_archive import restore_session as rs
from app.tiered_archive import restore_sizes as sizes
from app.tiered_archive import restore_universe as universe
from app.tiered_archive.codec import digest, epoch_us
from tests.test_tiered_archive import AT, SENSOR
from tests.test_tiered_restore_final_audit import audit, start
from tests.test_tiered_restore_session import apply, begin, log, plan
from tests.test_tiered_restore_session import target as restore_target

target = restore_target
SESSION = "00000000-0000-0000-0000-000000000099"


def config():
    return dict(
        protocol_version=1,
        device_id=SENSOR,
        session_id=SESSION,
        session_start_us=epoch_us(AT),
        sample_rate=380,
        channels=1,
        sample_bits=16,
        channel_labels=["leaf0"],
        calibration_version="cal-v1",
        firmware_version="fw-v1",
    )


def row(mode="DIRECT"):
    return dict(device_id=SENSOR, id=SESSION, config=config(), mode=mode)


def test_known_json_admission_pins_type_compression_version_before_output():
    table = Table("direct_session", MetaData(), Column("config", JSON))
    expr = sizes.known_config_size(table.c.config)
    compiled = expr.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "pg_catalog.json'::pg_catalog.regtype" in sql
    assert "pg_catalog.pg_column_compression" in sql and "IS NULL" in sql
    assert "pg_catalog.pg_column_size" in sql and "server_version_num" in compiled.params.values()
    assert "row_to_json" not in sql and "::text" not in sql
    assert sizes.KNOWN_CONFIG_BYTES == 2048
    other = Table("private_other", MetaData(), Column("payload", JSON))
    assert sizes.known_config_size(other.c.payload) is None


@pytest.mark.parametrize("mode", ["DIRECT", "DUAL"])
def test_existing_scientific_session_contract_accepts_supported_source_modes(mode):
    table = Table("direct_session", MetaData())
    sizes.validate_known_row(table, row(mode))


@pytest.mark.parametrize("mutation", ["extra", "device", "session", "rate", "mode", "labels"])
def test_known_config_uses_existing_protocol_and_exact_parent_identity(mutation):
    value = row()
    if mutation == "extra":
        value["config"]["password"] = "MUST-NOT-LEAK"
    elif mutation == "device":
        value["device_id"] = SESSION
    elif mutation == "session":
        value["id"] = SENSOR
    elif mutation == "rate":
        value["config"]["sample_rate"] = 2001
    elif mutation == "mode":
        value["mode"] = "UNKNOWN"
    elif mutation == "labels":
        value["config"]["channel_labels"] = ["bad-label"]
    with pytest.raises(ArchiveBlocked, match="scientific contract") as error:
        sizes.validate_known_row(Table("direct_session", MetaData()), value)
    assert "MUST-NOT-LEAK" not in str(error.value)
    assert error.value.__suppress_context__


def test_jsonb_is_never_a_plain_json_size_proof():
    table = Table("direct_session", MetaData(), Column("config", postgresql.JSONB))
    sql = str(sizes.known_config_size(table.c.config).compile(dialect=postgresql.dialect()))
    assert "pg_catalog.json'::pg_catalog.regtype" in sql
    assert "pg_catalog.jsonb'::" not in sql
    # Actual OID mismatch makes admission false; no JSONB numeric/string output
    # operation exists in this metadata predicate.
    assert "jsonb_each" not in sql and "::text" not in sql


@pytest.fixture(autouse=True)
def synthetic_owner(monkeypatch):
    monkeypatch.setattr(inputs, "ROOT_UID", os.geteuid())


def changes(capability="a" * 64):
    return dict(format=rs.MODE_FORMAT, fence_mode=cap.MODE, fence_capability_sha256=capability)


def test_headv3_binds_mode_capability_everywhere_and_finishes_bounded_audit(
    target, tmp_path, monkeypatch
):
    current = ["a" * 64]
    seen = []

    def verify(db, expected):
        seen.append(expected)
        if expected != current[0]:
            raise ArchiveBlocked("Synthetic origin capability changed")
        return dict(sha256=expected, authority={"mode": cap.MODE})

    monkeypatch.setattr(cap, "verify", verify)
    head, sha, descriptors = plan(
        tmp_path, [[[log("one")]], [[log("two")]]], changes=changes(), freeze=True
    )
    rs.validate_plan(head, sha)
    assert universe.domain(head)["fence_mode"] == cap.MODE
    assert universe.domain(head)["fence_capability_sha256"] == current[0]
    session = begin(head, sha)
    for descriptor in descriptors:
        apply(head, sha, session, descriptor)
    start(head, sha, session)
    audit(head, sha, session, descriptors[0])
    with target.connect() as db:
        from sqlalchemy import text

        assert db.execute(text("SELECT mode FROM greenmind_restore_fence")).scalar_one() == cap.MODE
    current[0] = "b" * 64
    with pytest.raises(ArchiveBlocked, match="capability changed"):
        audit(head, sha, session, descriptors[1])
    current[0] = "a" * 64
    assert audit(head, sha, session, descriptors[1])["session_content_audit_complete"]
    assert len(seen) > 4 and set(seen) == {"a" * 64}


@pytest.mark.parametrize(
    "update",
    [
        dict(format=rs.MODE_FORMAT),
        dict(fence_mode=cap.MODE, fence_capability_sha256="a" * 64),
        dict(format=rs.MODE_FORMAT, fence_mode="pg-always-v1", fence_capability_sha256="a" * 64),
        dict(format=rs.MODE_FORMAT, fence_mode=cap.MODE, fence_capability_sha256="true"),
    ],
)
def test_incomplete_or_silent_mode_changes_are_rejected_before_connection(
    target, tmp_path, monkeypatch, update
):
    head, _, _ = plan(tmp_path, [[[log("one")]]], changes=update)
    monkeypatch.setattr(rs, "connection", lambda *_: pytest.fail("Must not connect"))
    with pytest.raises(ArchiveBlocked):
        rs.begin_restore_session("unused", head, digest(head), checkpoint=lambda: None)


def test_v2_remains_default_always_without_origin_verifier(target, tmp_path, monkeypatch):
    monkeypatch.setattr(cap, "verify", lambda *_: pytest.fail("No implicit origin admission"))
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]])
    session = begin(head, sha)
    assert apply(head, sha, session, descriptors[0])["phase"] == "loaded"
    with target.connect() as db:
        from sqlalchemy import text

        assert (
            db.execute(text("SELECT mode FROM greenmind_restore_fence")).scalar_one()
            == "pg-always-v1"
        )


def test_mode_capability_changes_plan_and_scope_identity(target, tmp_path):
    head, _, _ = plan(tmp_path, [[[log("one")]]], changes=changes())
    other = deepcopy(head)
    other["fence_capability_sha256"] = "b" * 64
    assert digest(head) != digest(other)
    assert digest(rs.plan_scope(head)) != digest(rs.plan_scope(other))
    assert universe.domain(head) != universe.domain(other)


def test_oversized_known_config_is_rejected_before_any_value_fetch(target):
    import json

    from sqlalchemy import text

    from app.tiered_archive import restore_baseline as baseline

    with target.begin() as db:
        db.execute(
            text(
                "CREATE TABLE direct_session(device_id VARCHAR(36),id VARCHAR(36),config JSON,mode VARCHAR(10),PRIMARY KEY(device_id,id))"
            )
        )
        oversized = row()
        oversized["config"]["firmware_version"] = "x" * sizes.KNOWN_CONFIG_BYTES
        db.execute(
            text("INSERT INTO direct_session VALUES(:device,:id,:config,:mode)"),
            dict(device=SENSOR, id=SESSION, config=json.dumps(oversized["config"]), mode="DIRECT"),
        )
        table = Table("direct_session", MetaData(), autoload_with=db)
        baseline.ensure(db, {table.name: table}, dict(master_epoch=0), rs.schema_fingerprint)
    from sqlalchemy import event

    @event.listens_for(target, "before_cursor_execute")
    def no_values(conn, cursor, statement, parameters, context, many):
        assert not statement.lstrip().startswith(
            "SELECT direct_session.device_id, direct_session.id, direct_session.config"
        )

    with pytest.raises(ArchiveBlocked, match="raw byte size"), target.begin() as db:
        baseline.advance(db, {table.name: table}, dict(master_epoch=0), checkpoint=lambda: None)
