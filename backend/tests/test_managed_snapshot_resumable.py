# ruff: noqa: F811
# Imported pytest fixtures intentionally share names with test parameters.
"""Durable one-object snapshot readback and fail-closed resume."""

import hashlib
import json
from datetime import timedelta

import pytest

from app.raw_archive.policy import ArchiveBlocked, checksum
from app.raw_archive.scientific_capture import HEADER, TRAILER
from app.tiered_archive import backup
from app.tiered_archive.ledger import Ledger
from app.tiered_archive.managed_snapshot import SnapshotReadbackPending, cycle
from app.tiered_archive.pipeline import key_for
from app.tiered_archive.policy import POLICY_SHA256
from app.tiered_archive.runtime import candidate_from_json
from tests.test_managed_snapshot_cycle import managed  # noqa: F401
from tests.test_tiered_archive import bundle  # noqa: F401


def invoke(ledger, box, provider, clock, *, budget=64 * 1024**2):
    return cycle(
        ledger,
        box,
        provider,
        lambda: None,
        now=clock,
        dependency_guard=lambda *_: None,
        resumable_readback=True,
        readback_max_bytes=budget,
    )


def test_one_remote_object_per_run_survives_restart(managed, monkeypatch):
    ledger, box, provider, _, clock = managed
    calls = []
    original = box.download

    def download(key, path, size, *, snapshot=None):
        if snapshot is not None:
            calls.append((key, snapshot))
        return original(key, path, size, snapshot=snapshot)

    monkeypatch.setattr(box, "download", download)
    result = invoke(ledger, box, provider, clock)
    assert isinstance(result, SnapshotReadbackPending) and not result
    assert len(calls) == 1 and provider.posts == 1
    assert ledger.db.execute("SELECT state,proof FROM managed_snapshot").fetchone() == (
        "bound",
        None,
    )
    reopened = Ledger(ledger.root)
    try:
        for _ in range(30):
            before = len(calls)
            result = invoke(reopened, box, provider, clock)
            assert len(calls) - before <= 1
            if result == 101:
                break
            assert isinstance(result, SnapshotReadbackPending) and not result
            assert reopened.db.execute("SELECT state FROM managed_snapshot").fetchone() == (
                "bound",
            )
        else:
            pytest.fail("snapshot readback did not finish")
        assert provider.posts == 1 and provider.deletes == []
        assert reopened.db.execute("SELECT state FROM managed_snapshot").fetchone() == ("verified",)
        assert len(calls) >= 3
    finally:
        reopened.close()


def test_snapshot_identity_and_catalogue_tamper_block_resume(managed):
    ledger, box, provider, _, clock = managed
    assert not invoke(ledger, box, provider, clock)
    original = provider.rows[-1]["name"]
    provider.rows[-1]["name"] = "changed-name"
    with pytest.raises(ArchiveBlocked, match="identity"):
        invoke(ledger, box, provider, clock)
    provider.rows[-1]["name"] = original
    with ledger.db:
        ledger.db.execute(
            "UPDATE managed_snapshot SET catalog=? WHERE state='bound'",
            (json.dumps({"key": "staging/other"}),),
        )
    with pytest.raises(ArchiveBlocked):
        invoke(ledger, box, provider, clock)
    assert provider.posts == 1 and provider.deletes == []


def test_expired_evidence_requires_new_remote_readback(managed):
    ledger, box, provider, current, clock = managed
    assert not invoke(ledger, box, provider, clock)
    original_gets = box.gets
    current[0] += timedelta(hours=25)
    assert not invoke(ledger, box, provider, clock)
    assert box.gets == original_gets + 1
    assert ledger.db.execute("SELECT state FROM managed_snapshot").fetchone() == ("bound",)


def test_small_budget_never_reads_oversized_remote_file(managed):
    ledger, box, provider, _, clock = managed
    original_gets = box.gets
    result = invoke(ledger, box, provider, clock, budget=1)
    assert isinstance(result, SnapshotReadbackPending)
    assert box.gets == original_gets
    assert ledger.db.execute("SELECT state FROM managed_snapshot").fetchone() == ("bound",)


def test_more_than_512mib_of_snapshot_objects_progresses_one_part_at_a_time(
    managed, tmp_path, monkeypatch
):
    ledger, box, provider, _, clock = managed
    identity, raw = ledger.db.execute("SELECT id,receipt FROM bundle").fetchone()
    receipt = json.loads(raw)
    candidate = candidate_from_json(receipt["source"])
    sparse = tmp_path / "large.bin"
    with sparse.open("wb") as body:
        body.truncate(32 * 1024**2)
    sha = checksum(sparse)
    ref = {
        "key": key_for(candidate, sha, "bin"),
        "sha256": sha,
        "bytes": 32 * 1024**2,
        "format": "bin",
    }
    box.publish(ref["key"], sparse)
    receipt["files"] += [ref] * 17
    document = {
        "version": 1,
        "policy": POLICY_SHA256,
        "candidate": receipt["source"],
        "identity": receipt["candidate"],
        "files": receipt["files"],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")))
    manifest_sha = checksum(manifest_path)
    receipt["manifest"] = {
        "key": key_for(candidate, manifest_sha, "json"),
        "sha256": manifest_sha,
        "bytes": manifest_path.stat().st_size,
        "format": "json",
    }
    box.publish(receipt["manifest"]["key"], manifest_path)
    with ledger.db:
        ledger.db.execute("UPDATE bundle SET receipt=? WHERE id=?", (json.dumps(receipt), identity))
    calls = []
    original = box.download

    def download(key, path, size, *, snapshot=None):
        if snapshot is not None:
            calls.append(size)
        return original(key, path, size, snapshot=snapshot)

    monkeypatch.setattr(box, "download", download)
    for _ in range(40):
        before = len(calls)
        result = invoke(ledger, box, provider, clock, budget=32 * 1024**2)
        assert len(calls) - before <= 1
        if result == 101:
            break
        assert isinstance(result, SnapshotReadbackPending)
    else:
        pytest.fail("large snapshot readback did not finish")
    assert sum(calls) > 512 * 1024**2 and provider.posts == 1 and not provider.deletes


@pytest.mark.parametrize("valid", [True, False])
def test_science_ciphertext_requires_key_and_is_typed_before_verified(
    managed, tmp_path, monkeypatch, valid
):
    ledger, box, provider, _, clock = managed
    box.identity = "sftp://u1@u1.your-storagebox.de:22/archive"
    identity, raw = ledger.db.execute("SELECT id,receipt FROM bundle").fetchone()
    receipt = json.loads(raw)
    receipt["destination"] = box.identity
    plaintext = tmp_path / "science.pgcopy"
    plaintext.write_bytes(HEADER + (TRAILER if valid else b"\x00\x00"))
    ciphertext = tmp_path / "science.bin"
    key = b"k" * 32
    meta = backup.encrypt(plaintext, ciphertext, key, checkpoint=lambda: None)
    prefix = "staging/tiered/11111111-1111-4111-8111-111111111111/science/2026-10-01/"
    ref = {**meta, "key": prefix + meta["sha256"] + ".bin"}
    box.publish(ref["key"], ciphertext)
    part = {
        "table": "wav_feature",
        "sequence": 0,
        "first_row": 0,
        "rows": 0,
        "start": 19,
        "end": 19,
        "bytes": 21,
        "sha256": checksum(plaintext),
    }
    binding = {
        "capture_sha256": "a" * 64,
        "index_sha256": "b" * 64,
        "capture_id": "c" * 32,
        "position": 0,
        "part": part,
        "destination": box.identity,
        "key_prefix": prefix,
    }
    encoded = json.dumps(binding, sort_keys=True, separators=(",", ":"))
    with ledger.db:
        ledger.db.execute("UPDATE bundle SET receipt=? WHERE id=?", (json.dumps(receipt), identity))
        ledger.db.execute(
            "CREATE TABLE science_v3_part (token TEXT PRIMARY KEY,binding TEXT NOT NULL,state TEXT NOT NULL,reference TEXT NOT NULL)"
        )
        ledger.db.execute(
            "INSERT INTO science_v3_part VALUES(?,?,'verified',?)",
            (
                hashlib.sha256(encoded.encode()).hexdigest(),
                encoded,
                json.dumps(ref, sort_keys=True, separators=(",", ":")),
            ),
        )
    monkeypatch.delenv("TIERED_ARCHIVE_BACKUP_KEY_SHA256", raising=False)
    monkeypatch.delenv("TIERED_ARCHIVE_BACKUP_KEY_FILE", raising=False)
    for _ in range(20):
        try:
            assert not invoke(ledger, box, provider, clock)
        except ArchiveBlocked as error:
            assert "encryption key required" in str(error)
            break
    else:
        pytest.fail("science snapshot did not require a key")
    assert ledger.db.execute("SELECT state FROM managed_snapshot").fetchone() == ("bound",)
    key_path = tmp_path / "archive-key"
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    monkeypatch.setenv("TIERED_ARCHIVE_BACKUP_KEY_FILE", str(key_path))
    monkeypatch.setenv("TIERED_ARCHIVE_BACKUP_KEY_SHA256", hashlib.sha256(key).hexdigest())
    for _ in range(20):
        try:
            result = invoke(ledger, box, provider, clock)
        except ArchiveBlocked:
            if valid:
                raise
            assert ledger.db.execute("SELECT state FROM managed_snapshot").fetchone() == ("bound",)
            return
        if result == 101:
            break
        assert isinstance(result, SnapshotReadbackPending)
    else:
        pytest.fail("typed science readback did not finish")
    assert valid and ledger.db.execute("SELECT state FROM managed_snapshot").fetchone() == (
        "verified",
    )
    from app.tiered_archive.managed_snapshot import _completed_readback

    intent, raw_proof = ledger.db.execute("SELECT intent,proof FROM managed_snapshot").fetchone()
    assert _completed_readback(ledger, intent, json.loads(raw_proof))
    monkeypatch.setenv("TIERED_ARCHIVE_BACKUP_KEY_SHA256", "0" * 64)
    with pytest.raises(ArchiveBlocked, match="key binding"):
        _completed_readback(ledger, intent, json.loads(raw_proof))


def test_unknown_provider_action_and_progress_tamper_block(managed, monkeypatch):
    ledger, box, provider, _, clock = managed
    assert not invoke(ledger, box, provider, clock)
    original = provider.require_idle_actions

    def unknown():
        raise ArchiveBlocked("Unknown provider action")

    monkeypatch.setattr(provider, "require_idle_actions", unknown)
    gets = box.gets
    with pytest.raises(ArchiveBlocked, match="Unknown provider action"):
        invoke(ledger, box, provider, clock)
    assert box.gets == gets
    monkeypatch.setattr(provider, "require_idle_actions", original)
    for _ in range(20):
        assert not invoke(ledger, box, provider, clock)
        if ledger.db.execute("SELECT count(*) FROM managed_snapshot_readback_item").fetchone()[0]:
            break
    else:
        pytest.fail("no item progress")
    with ledger.db:
        ledger.db.execute("UPDATE managed_snapshot_readback_item SET item_sha256=?", ("0" * 64,))
    with pytest.raises(ArchiveBlocked, match="progress differs"):
        invoke(ledger, box, provider, clock)
    assert provider.posts == 1 and not provider.deletes


def test_interrupted_download_is_retried_without_new_snapshot(managed, monkeypatch):
    ledger, box, provider, _, clock = managed
    original = box.download
    interrupted = [False]

    def download(key, path, size, *, snapshot=None):
        original(key, path, size, snapshot=snapshot)
        if snapshot is not None and not interrupted[0]:
            interrupted[0] = True
            raise OSError("interrupted after download")

    monkeypatch.setattr(box, "download", download)
    with pytest.raises(OSError, match="interrupted"):
        invoke(ledger, box, provider, clock)
    assert ledger.db.execute("SELECT count(*) FROM managed_snapshot_readback_cache").fetchone() == (
        0,
    )
    for _ in range(30):
        result = invoke(ledger, box, provider, clock)
        if result == 101:
            break
        assert isinstance(result, SnapshotReadbackPending)
    else:
        pytest.fail("interrupted snapshot readback did not finish")
    assert provider.posts == 1 and not provider.deletes


def test_verified_science_whole_and_all_parts_are_read_back(managed, tmp_path, monkeypatch):
    from app.tiered_archive.registry_v5 import DDL
    from tests.raw_archive.test_scientific_v3_archive import run, setup_job, whole

    ledger, box, provider, _, clock = managed
    box.identity = "sftp://u1@u1.your-storagebox.de:22/archive"
    args, objects = setup_job(tmp_path)
    args["publisher"].identity = box.identity
    args["verifier"].identity = box.identity
    args["key_prefix"] = "staging/tiered/11111111-1111-4111-8111-111111111111/science/2026-10-01/"
    run(args)
    args["position"] = 1
    run(args)
    whole(args)
    for key, data in objects.items():
        source = tmp_path / "ciphertext"
        source.write_bytes(data)
        box.publish(key, source)
    identity, raw = ledger.db.execute("SELECT id,receipt FROM bundle").fetchone()
    receipt = json.loads(raw)
    receipt["destination"] = box.identity
    with ledger.db:
        ledger.db.execute("UPDATE bundle SET receipt=? WHERE id=?", (json.dumps(receipt), identity))
        for table in ("science_v3_part", "science_v3_whole"):
            ledger.db.execute(DDL[table])
            for row in args["ledger"].db.execute("SELECT * FROM " + table):
                ledger.db.execute("INSERT INTO " + table + " VALUES(?,?,?,?)", row)
    key_path = tmp_path / "archive-key"
    key_path.write_bytes(b"k" * 32)
    key_path.chmod(0o600)
    monkeypatch.setenv("TIERED_ARCHIVE_BACKUP_KEY_FILE", str(key_path))
    monkeypatch.setenv("TIERED_ARCHIVE_BACKUP_KEY_SHA256", hashlib.sha256(b"k" * 32).hexdigest())
    for _ in range(30):
        result = invoke(ledger, box, provider, clock)
        if result == 101:
            break
        assert isinstance(result, SnapshotReadbackPending)
    else:
        pytest.fail("whole science inventory did not verify")
    assert ledger.db.execute("SELECT state FROM managed_snapshot").fetchone() == ("verified",)


def test_missed_48_hour_cycle_refreshes_both_survivors_before_rotation(managed, monkeypatch):
    from app.tiered_archive.pipeline import verify_snapshot

    ledger, box, provider, current, clock = managed
    for expected in (101, 102):
        for _ in range(30):
            result = invoke(ledger, box, provider, clock)
            if result == expected:
                break
            assert isinstance(result, SnapshotReadbackPending)
        else:
            pytest.fail("initial snapshot did not finish")
        if expected == 101:
            raw = ledger.db.execute("SELECT receipt FROM bundle").fetchone()[0]
            verify_snapshot(
                candidate_from_json(json.loads(raw)["source"]),
                box,
                ledger,
                provider,
                101,
                checkpoint=lambda: None,
                now=clock(),
            )
        current[0] += timedelta(hours=12)

    for _ in range(30):
        result = invoke(ledger, box, provider, clock)
        if result == 103:
            break
        assert isinstance(result, SnapshotReadbackPending)
    else:
        pytest.fail("third snapshot did not complete")
    assert provider.posts == 3 and not provider.deletes
    before_receipt = json.loads(ledger.db.execute("SELECT receipt FROM bundle").fetchone()[0])
    old_proofs = {
        json.loads(row[0])["id"]: json.loads(row[1])["checked_at"]
        for row in ledger.db.execute(
            "SELECT snapshot,proof FROM managed_snapshot WHERE state='verified'"
        )
    }
    current[0] += timedelta(hours=48)
    calls = []
    original = box.download

    def download(key, path, size, *, snapshot=None):
        if snapshot is not None:
            calls.append(snapshot)
        return original(key, path, size, snapshot=snapshot)

    monkeypatch.setattr(box, "download", download)
    checked_tamper = False
    for _ in range(60):
        count = len(calls)
        invoke(ledger, box, provider, clock)
        assert len(calls) - count <= 1
        if provider.deletes:
            break
        current_proofs = {
            json.loads(row[0])["id"]: json.loads(row[1])["checked_at"]
            for row in ledger.db.execute(
                "SELECT snapshot,proof FROM managed_snapshot WHERE state='verified'"
            )
        }
        if not checked_tamper and all(current_proofs[i] != old_proofs[i] for i in (102, 103)):
            identity, raw = ledger.db.execute("SELECT id,receipt FROM bundle").fetchone()
            changed = json.loads(raw)
            changed["files"][0]["sha256"] = "0" * 64
            with ledger.db:
                ledger.db.execute(
                    "UPDATE bundle SET receipt=? WHERE id=?", (json.dumps(changed), identity)
                )
            with pytest.raises(ArchiveBlocked, match="exact bundle"):
                invoke(ledger, box, provider, clock)
            assert not provider.deletes
            with ledger.db:
                ledger.db.execute("UPDATE bundle SET receipt=? WHERE id=?", (raw, identity))
            checked_tamper = True
    else:
        pytest.fail("stale survivor readbacks did not complete")
    assert provider.deletes == [101]
    assert checked_tamper
    rows = ledger.db.execute(
        "SELECT snapshot,proof FROM managed_snapshot WHERE state='verified'"
    ).fetchall()
    refreshed = {json.loads(row[0])["id"]: json.loads(row[1])["checked_at"] for row in rows}
    assert all(refreshed[i] != old_proofs[i] for i in (102, 103))
    after_receipt = json.loads(ledger.db.execute("SELECT receipt FROM bundle").fetchone()[0])
    assert after_receipt["first_verified_at"] == before_receipt["first_verified_at"]
    assert after_receipt["snapshot"]["provider_id"] == 103
    assert (
        after_receipt["source"] == before_receipt["source"]
        and after_receipt["files"] == before_receipt["files"]
    )
    assert set(range(1, 8)) <= {row["id"] for row in provider.rows}


def test_cache_cap_survives_two_rotations_and_preserves_live_caches(managed, tmp_path, monkeypatch):
    from app.raw_archive import coordination as coord
    from app.raw_archive.coordination import lease
    from app.tiered_archive import managed_snapshot as module

    ledger, box, provider, current, clock = managed
    coordination = tmp_path / "coordination"
    coordination.mkdir(mode=0o700)
    monkeypatch.setenv("RAW_ARCHIVE_COORDINATION_DIR", str(coordination))
    parent = ledger.root / "snapshot-readback"
    with lease("catalog"):
        for expected in (101, 102, 103):
            for _ in range(30):
                result = invoke(ledger, box, provider, clock)
                if result == expected:
                    break
            else:
                pytest.fail("initial snapshot readback did not finish")
            if expected != 103:
                current[0] += timedelta(hours=12)
        sizes = [p.stat().st_size for p in parent.glob("*/*") if p.is_file()]
        marker_bytes = sum(p.stat().st_size for p in parent.glob("*/.owner.json"))
        initial = sum(sizes) + marker_bytes
        cap = initial + max(sizes)
        monkeypatch.setattr(module, "_READBACK_CACHE_MAX_BYTES", cap)
        advanced = False
        for _ in range(100):
            invoke(ledger, box, provider, clock)
            used = sum(
                p.stat().st_size for d in parent.iterdir() for p in d.iterdir() if p.is_file()
            )
            assert used <= cap
            if provider.deletes == [101, 102]:
                break
            if not advanced and provider.deletes == [101]:
                intent = ledger.db.execute(
                    "SELECT intent FROM managed_snapshot WHERE state='deleted'"
                ).fetchone()[0]
                deleted_dir = parent / intent
                identity, raw_receipt = ledger.db.execute(
                    "SELECT id,receipt FROM bundle"
                ).fetchone()
                referenced = json.loads(raw_receipt)
                referenced["snapshot"] = {"provider_id": 101}
                with ledger.db:
                    ledger.db.execute(
                        "UPDATE bundle SET receipt=? WHERE id=?", (json.dumps(referenced), identity)
                    )
                assert not module._cache_safe_to_clean(ledger, intent)
                with ledger.db:
                    ledger.db.execute(
                        "UPDATE bundle SET receipt=? WHERE id=?", (raw_receipt, identity)
                    )
                with ledger.db:
                    ledger.db.execute(
                        "INSERT INTO managed_snapshot_pin VALUES(?,?,?,?,?,?,'active',?,NULL,NULL)",
                        (
                            "f" * 32,
                            intent,
                            101,
                            "restoreinput",
                            "a" * 64,
                            "{}",
                            clock().isoformat(),
                        ),
                    )
                _, owners = module._cache_inventory(ledger, parent)
                token = coord._BOUND_JOB_LOCK.set(None)
                try:
                    with pytest.raises(ArchiveBlocked, match="jobs lock required"):
                        module._cache_cleanup(ledger, parent, intent, owners[intent])
                finally:
                    coord._BOUND_JOB_LOCK.reset(token)
                with pytest.raises(ArchiveBlocked, match="still referenced"):
                    module._cache_cleanup(ledger, parent, intent, owners[intent])
                assert deleted_dir.is_dir()
                with ledger.db:
                    ledger.db.execute("UPDATE managed_snapshot_pin SET state='uncertain'")
                with pytest.raises(ArchiveBlocked, match="still referenced"):
                    module._cache_cleanup(ledger, parent, intent, owners[intent])
                with ledger.db:
                    ledger.db.execute(
                        "UPDATE managed_snapshot_pin SET state='released',completed_at=?,completion_sha256=?",
                        (clock().isoformat(), "b" * 64),
                    )
                    ledger.db.execute(
                        "INSERT INTO managed_snapshot_pin_event(pin_id,state,at,evidence) VALUES(?,?,?,?)",
                        ("f" * 32, "released", clock().isoformat(), "b" * 64),
                    )
                original_unlink = module.os.unlink
                calls = [0]

                def interrupted_unlink(
                    path, *args, _calls=calls, _unlink=original_unlink, **kwargs
                ):
                    if kwargs.get("dir_fd") is not None:
                        _calls[0] += 1
                        if _calls[0] == 2:
                            raise OSError("simulated cleanup interruption")
                    return _unlink(path, *args, **kwargs)

                with monkeypatch.context() as patch:
                    patch.setattr(module.os, "unlink", interrupted_unlink)
                    with pytest.raises(ArchiveBlocked, match="cleanup interrupted"):
                        module._cache_cleanup(ledger, parent, intent, owners[intent])
                assert ledger.db.execute(
                    "SELECT state FROM managed_snapshot_cache_owner WHERE intent=?", (intent,)
                ).fetchone() == ("cleaning",)
                module._cache_reserve(ledger, parent, 0, "0" * 32)
                assert not deleted_dir.exists()
                current[0] += timedelta(hours=12)
                advanced = True
        else:
            pytest.fail(
                f"two cache-bounded rotations did not complete: {provider.deletes}, {provider.posts}, {advanced}"
            )
        rows = ledger.db.execute("SELECT intent,state FROM managed_snapshot_cache_owner").fetchall()
        assert any(state == "cleaned" for _, state in rows)
        for intent, state in rows:
            managed_state = ledger.db.execute(
                "SELECT state FROM managed_snapshot WHERE intent=?", (intent,)
            ).fetchone()[0]
            if managed_state != "deleted":
                assert state == "active" and (parent / intent).is_dir()
    assert set(range(1, 8)) <= {row["id"] for row in provider.rows}


def test_unknown_legacy_cache_and_unknown_files_are_never_removed(managed):
    ledger, box, provider, _, clock = managed
    parent = ledger.root / "snapshot-readback"
    parent.mkdir(mode=0o700)
    unknown = parent / ("a" * 32)
    unknown.mkdir(mode=0o700)
    original = unknown / "unowned"
    original.write_text("preserve")
    with pytest.raises(ArchiveBlocked, match="Unknown readback cache ownership"):
        invoke(ledger, box, provider, clock)
    assert original.read_text() == "preserve" and not provider.deletes


def test_active_cache_and_unknown_owned_directory_file_are_retained(managed):
    from app.tiered_archive import managed_snapshot as module

    ledger, box, provider, _, clock = managed
    assert not invoke(ledger, box, provider, clock)
    parent = ledger.root / "snapshot-readback"
    intent = ledger.db.execute(
        "SELECT intent FROM managed_snapshot WHERE state='bound'"
    ).fetchone()[0]
    with pytest.raises(ArchiveBlocked, match="budget exhausted"):
        module._cache_reserve(ledger, parent, module._READBACK_CACHE_MAX_BYTES, intent)
    directory = parent / intent
    assert directory.is_dir()
    unknown = directory / ("b" * 64)
    unknown.write_bytes(b"unknown")
    unknown.chmod(0o400)
    with pytest.raises(ArchiveBlocked, match="Unknown readback cache file"):
        invoke(ledger, box, provider, clock)
    assert unknown.read_bytes() == b"unknown" and not provider.deletes


def test_disk_reserve_holds_before_cache_download(managed, monkeypatch):
    from app.tiered_archive import managed_snapshot as module

    ledger, box, provider, _, clock = managed
    monkeypatch.setattr(
        module.shutil,
        "disk_usage",
        lambda _: type("Usage", (), {"free": module._READBACK_DISK_RESERVE - 1})(),
    )
    gets = box.gets
    with pytest.raises(ArchiveBlocked, match="disk safety reserve"):
        invoke(ledger, box, provider, clock)
    assert box.gets == gets and not provider.deletes
