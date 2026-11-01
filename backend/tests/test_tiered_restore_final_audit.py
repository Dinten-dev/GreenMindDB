"""Bounded real-SQLite final audits; explicit synthetic owner/PG metadata adapters."""

import os

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import restore_inputs as inputs
from app.tiered_archive import restore_session as rs
from tests.test_tiered_restore import URL
from tests.test_tiered_restore_session import (
    GATEWAY,
    apply,
    begin,
    log,
    plan,
)
from tests.test_tiered_restore_session import (
    target as restore_target,
)

target = restore_target

OTHER_ORG = "20000000-0000-0000-0000-000000000001"
OTHER_GATEWAY = "20000000-0000-0000-0000-000000000002"
OTHER_ZONE = "20000000-0000-0000-0000-000000000003"


@pytest.fixture(autouse=True)
def synthetic_operator(monkeypatch):
    monkeypatch.setattr(inputs, "ROOT_UID", os.geteuid())


def start(head, sha, session):
    return rs.begin_restore_audit(URL, head, sha, session, checkpoint=lambda: None)


def audit(head, sha, session, descriptor, checkpoint=lambda: None):
    return rs.audit_restore_batch(URL, head, sha, session, *descriptor, checkpoint=checkpoint)


def test_loaded_then_bounded_final_audit_and_exact_replays(target, tmp_path):
    head, sha, descriptors = plan(
        tmp_path, [[[log("one")]], [[log("two")], [log("three")]]], freeze=True
    )
    session = begin(head, sha)
    for descriptor in descriptors:
        loaded = apply(head, sha, session, descriptor)
    assert loaded["phase"] == "loaded" and not loaded["full_target_content_audit_complete"]
    epoch = start(head, sha, session)["audit_epoch"]
    first = audit(head, sha, session, descriptors[0])
    assert first["phase"] == "auditing" and not first["session_content_audit_complete"]
    assert audit(head, sha, session, descriptors[0])["replayed"]
    final = audit(head, sha, session, descriptors[1])
    assert final["phase"] == "verified" and final["session_content_audit_complete"]
    assert final["all_registered_sessions_verified"] and final["audit_epoch"] == epoch
    assert final["audited_pages"] == 3 and final["audited_rows"] == 3
    assert final["target_data_mutations"] == 0 and final["production_ready"] is False
    assert audit(head, sha, session, descriptors[1])["replayed"]
    assert audit(head, sha, session, descriptors[0])["phase"] == "verified"


def test_all_load_sessions_must_finish_before_global_audit(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]], [[log("two")]]], freeze=True)
    session = begin(head, sha)
    apply(head, sha, session, descriptors[0])
    with pytest.raises(ArchiveBlocked, match="Every registered"):
        start(head, sha, session)
    apply(head, sha, session, descriptors[1])
    assert start(head, sha, session)["phase"] == "auditing"


def test_mutation_before_audit_freeze_is_detected(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]], freeze=True)
    session = begin(head, sha)
    apply(head, sha, session, descriptors[0])
    with target.begin() as db:
        db.execute(text("UPDATE ingest_log SET payload=x'00'"))
    with pytest.raises(ArchiveBlocked, match="mutation"):
        start(head, sha, session)
    with target.connect() as db:
        assert (
            db.execute(text("SELECT state FROM greenmind_restore_session")).scalar_one() == "loaded"
        )


def test_ordinary_dml_between_audit_calls_is_kernel_db_blocked(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]], [[log("two")]]], freeze=True)
    session = begin(head, sha)
    for descriptor in descriptors:
        apply(head, sha, session, descriptor)
    start(head, sha, session)
    audit(head, sha, session, descriptors[0])
    with pytest.raises(IntegrityError), target.begin() as db:
        db.execute(text("UPDATE ingest_log SET payload=x'00' WHERE measurement_id='one'"))
    assert audit(head, sha, session, descriptors[1])["phase"] == "verified"


def test_changed_trigger_catalog_between_audits_never_finishes(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]], [[log("two")]]], freeze=True)
    session = begin(head, sha)
    for descriptor in descriptors:
        apply(head, sha, session, descriptor)
    start(head, sha, session)
    audit(head, sha, session, descriptors[0])
    with target.begin() as db:
        db.execute(text('DROP TRIGGER "greenmind_restore_row_v1_ingest_log_update"'))
        db.execute(text("UPDATE ingest_log SET payload=x'00' WHERE measurement_id='one'"))
    with pytest.raises(ArchiveBlocked, match="catalog"):
        audit(head, sha, session, descriptors[1])
    with target.connect() as db:
        assert (
            db.execute(text("SELECT audit_pages FROM greenmind_restore_session")).scalar_one() == 1
        )
        assert (
            db.execute(text("SELECT state FROM greenmind_restore_session")).scalar_one()
            == "auditing"
        )


def test_audit_out_of_order_or_unfrozen_files_do_not_advance(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]], [[log("two")]]], freeze=True)
    session = begin(head, sha)
    for descriptor in descriptors:
        apply(head, sha, session, descriptor)
    start(head, sha, session)
    with pytest.raises(ArchiveBlocked, match="sequence"):
        audit(head, sha, session, descriptors[1])
    descriptors[0][0].chmod(0o600)  # own synthetic operator copy; never a production original
    with pytest.raises(ArchiveBlocked, match="0400"):
        audit(head, sha, session, descriptors[0])
    with target.connect() as db:
        assert (
            db.execute(text("SELECT audit_pages FROM greenmind_restore_session")).scalar_one() == 0
        )


def test_33_pages_have_33_bounded_load_and_audit_calls(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log(str(i))]] for i in range(33)], freeze=True)
    session = begin(head, sha)
    for descriptor in descriptors:
        apply(head, sha, session, descriptor)
    start(head, sha, session)
    for descriptor in descriptors:
        result = audit(head, sha, session, descriptor)
    assert result["session_content_audit_complete"] and result["audited_pages"] == 33
    assert result["audited_rows"] == 33 and not result["production_ready"]


def test_two_organizations_share_target_and_audit_after_joint_load(target, tmp_path):
    with target.begin() as db:
        db.execute(
            text("INSERT INTO zone VALUES(:zone,:org)"), dict(zone=OTHER_ZONE, org=OTHER_ORG)
        )
        db.execute(
            text("INSERT INTO gateway VALUES(:id,:zone)"), dict(id=OTHER_GATEWAY, zone=OTHER_ZONE)
        )
    first_root, second_root = tmp_path / "org-one", tmp_path / "org-two"
    first_root.mkdir()
    second_root.mkdir()
    first, first_sha, first_pages = plan(first_root, [[[log("one")]]], freeze=True)
    second, second_sha, second_pages = plan(
        second_root, [[[log("two", gateway=OTHER_GATEWAY)]]], organization=OTHER_ORG, freeze=True
    )
    first_id = begin(first, first_sha)
    apply(first, first_sha, first_id, first_pages[0])
    second_id = begin(
        second, second_sha
    )  # same table is already nonempty and independently journalled
    apply(second, second_sha, second_id, second_pages[0])
    assert first_id != second_id
    start(first, first_sha, first_id)
    result = audit(first, first_sha, first_id, first_pages[0])
    assert (
        result["session_content_audit_complete"] and not result["all_registered_sessions_verified"]
    )
    start(second, second_sha, second_id)
    result = audit(second, second_sha, second_id, second_pages[0])
    assert result["all_registered_sessions_verified"]
    with target.connect() as db:
        assert db.execute(text("SELECT rows FROM greenmind_restore_total")).scalar_one() == 2
        assert db.execute(
            text("SELECT rows FROM greenmind_restore_session ORDER BY organization")
        ).scalars().all() == [1, 1]
        assert db.execute(text("SELECT count(*) FROM ingest_log")).scalar_one() == 2
    with pytest.raises(ArchiveBlocked, match="phase"):
        begin(first, first_sha)  # no new begin/ensure after audit freeze


def test_no_growing_target_counts_or_master_scans_during_load_or_final_audit(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]], freeze=True)
    session = begin(head, sha)

    @event.listens_for(target, "before_cursor_execute")
    def bounded(conn, cursor, statement, parameters, context, many):
        sql = " ".join(statement.lower().split())
        assert "count(*) from ingest_log" not in sql
        assert "count(*) from gateway" not in sql
        assert "count(*) from zone" not in sql
        # Master values were already baseline-captured; no unconditional raw master read remains.
        assert not (sql.startswith("select gateway.") and " where " not in sql)
        assert not (sql.startswith("select zone.") and " where " not in sql)

    apply(head, sha, session, descriptors[0])
    start(head, sha, session)
    assert audit(head, sha, session, descriptors[0])["phase"] == "verified"


def test_audit_checkpoint_failure_does_not_commit_partial_progress(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]], freeze=True)
    session = begin(head, sha)
    apply(head, sha, session, descriptors[0])
    start(head, sha, session)

    def stop():
        raise RuntimeError("synthetic checkpoint")

    with pytest.raises(RuntimeError):
        audit(head, sha, session, descriptors[0], checkpoint=stop)
    with target.connect() as db:
        assert (
            db.execute(text("SELECT audit_pages FROM greenmind_restore_session")).scalar_one() == 0
        )
    assert audit(head, sha, session, descriptors[0])["phase"] == "verified"


def test_other_dataset_write_is_fenced_before_that_dataset_has_a_session(target, tmp_path):
    with target.begin() as db:
        db.execute(
            text(
                "CREATE TABLE gateway_state_report(id TEXT PRIMARY KEY,gateway_id TEXT REFERENCES gateway(id),payload BLOB)"
            )
        )
        db.execute(
            text(
                "CREATE TRIGGER cross_dataset AFTER INSERT ON ingest_log BEGIN INSERT INTO gateway_state_report VALUES('shadow',NEW.gateway_id,x'00'); END"
            )
        )
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]], freeze=True)
    session = begin(head, sha)
    with pytest.raises(ArchiveBlocked, match="unexpected mutations"):
        apply(head, sha, session, descriptors[0])
    with target.connect() as db:
        assert db.execute(text("SELECT count(*) FROM ingest_log")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM gateway_state_report")).scalar_one() == 0


def test_dataset_b_trigger_cannot_mutate_committed_dataset_a(target, tmp_path):
    with target.begin() as db:
        db.execute(
            text(
                "CREATE TABLE gateway_state_report(id TEXT PRIMARY KEY,gateway_id TEXT REFERENCES gateway(id),payload BLOB)"
            )
        )
        db.execute(
            text(
                "CREATE TRIGGER change_a AFTER INSERT ON gateway_state_report BEGIN UPDATE ingest_log SET payload=x'00'; END"
            )
        )
    a, b = tmp_path / "dataset-a", tmp_path / "dataset-b"
    a.mkdir()
    b.mkdir()
    first, sha1, pages1 = plan(a, [[[log("A")]]], freeze=True)
    second, sha2, pages2 = plan(
        b,
        [[[dict(id="B", gateway_id=GATEWAY, payload=b"correct")]]],
        dataset="gateway_state_report",
        freeze=True,
    )
    first_id = begin(first, sha1)
    apply(first, sha1, first_id, pages1[0])
    second_id = begin(second, sha2)
    with pytest.raises(ArchiveBlocked, match="unexpected mutations"):
        apply(second, sha2, second_id, pages2[0])
    assert apply(first, sha1, first_id, pages1[0])["replayed"]
    with target.connect() as db:
        assert db.execute(text("SELECT count(*) FROM gateway_state_report")).scalar_one() == 0


def test_other_approved_master_trigger_write_is_also_fenced(target, tmp_path):
    with target.begin() as db:
        db.execute(text("CREATE TABLE captured_assignment(id INTEGER PRIMARY KEY,context TEXT)"))
        db.execute(text("INSERT INTO captured_assignment VALUES(1,'captured')"))
        db.execute(
            text(
                "CREATE TRIGGER change_master AFTER INSERT ON ingest_log BEGIN UPDATE captured_assignment SET context='changed'; END"
            )
        )
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]], freeze=True)
    session = begin(head, sha)
    with pytest.raises(ArchiveBlocked, match="unexpected mutations"):
        apply(head, sha, session, descriptors[0])
    with target.connect() as db:
        assert (
            db.execute(text("SELECT context FROM captured_assignment")).scalar_one() == "captured"
        )


def test_new_unknown_user_relation_after_plan_pin_blocks_every_future_step(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]], freeze=True)
    session = begin(head, sha)
    with target.begin() as db:
        db.execute(text("CREATE TABLE unknown_application_data(id INTEGER PRIMARY KEY)"))
    with pytest.raises(ArchiveBlocked, match="Unknown or missing"):
        apply(head, sha, session, descriptors[0])
    with target.connect() as db:
        assert db.execute(text("SELECT count(*) FROM ingest_log")).scalar_one() == 0


def test_unknown_user_relation_blocks_before_global_freeze(target, tmp_path):
    head, sha, descriptors = plan(tmp_path, [[[log("one")]]], freeze=True)
    session = begin(head, sha)
    apply(head, sha, session, descriptors[0])
    with target.begin() as db:
        db.execute(
            text("CREATE TABLE greenmind_restore_unknown_application(id INTEGER PRIMARY KEY)")
        )
    with pytest.raises(ArchiveBlocked, match="Unknown or missing"):
        start(head, sha, session)
    with target.connect() as db:
        assert (
            db.execute(text("SELECT phase FROM greenmind_restore_fence")).scalar_one() == "loading"
        )
