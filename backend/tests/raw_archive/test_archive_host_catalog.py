"""Independent recovery and resource pauses cannot become deletion acceptance."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.raw_archive import archive_host_catalog as worker
from app.raw_archive import wav_catalog as catalog
from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked

from .test_wav_catalog import fixture_bundle


@pytest.fixture
def configured(monkeypatch):
    for key, value in {
        "RAW_ARCHIVE_WORKER_ROLE": "archive",
        "RAW_ARCHIVE_ENVIRONMENT": "production",
        "RAW_ARCHIVE_LEASE_HOST": "188.245.247.156",
        "RAW_ARCHIVE_ENABLED": "false",
        "RAW_ARCHIVE_DELETE_ENABLED": "false",
        "RAW_ARCHIVE_GATEWAY_DATABASE_URL": "postgresql://greenmind_wav_metadata_b4ae52d674aa@127.0.0.1:5432/archive",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(worker, "catalog_lease", lambda: nullcontext(lambda: None))


class Probe:
    def __init__(self, ready):
        self.ready = ready
        self.last = {"reason": "source_memory"}
        self.closed = False

    def __call__(self):
        return self.ready

    def close(self):
        self.closed = True


def test_source_pause_opens_no_sql_and_creates_no_stage(tmp_path, monkeypatch, configured):
    probe = Probe(False)
    monkeypatch.setattr(worker, "health_probe", lambda: probe)
    monkeypatch.setattr(
        worker, "create_engine", lambda *a, **kw: pytest.fail("No SQL during pause")
    )
    output = tmp_path / "new"
    with pytest.raises(SafetyPause, match="source_memory"):
        worker.export_source("gateway", output)
    assert not output.exists() and probe.closed


def test_wrong_source_role_rejected_before_sql(tmp_path, monkeypatch, configured):
    monkeypatch.setenv("RAW_ARCHIVE_GATEWAY_DATABASE_URL", "postgresql://admin@127.0.0.1/archive")
    monkeypatch.setattr(worker, "create_engine", lambda *a, **kw: pytest.fail("No admin SQL"))
    with pytest.raises(ArchiveBlocked, match="narrow reader"):
        worker.export_source("gateway", tmp_path / "new")


def test_guards_cannot_be_weakened(monkeypatch, configured):
    monkeypatch.setenv("RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB", "1")
    monkeypatch.setenv("RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB", "1")
    monkeypatch.setenv("RAW_ARCHIVE_SOURCE_MAX_LOAD", "99")
    worker.configured()
    assert worker.os.environ["RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB"] == "640"
    assert worker.os.environ["RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB"] == "800"
    assert worker.os.environ["RAW_ARCHIVE_SOURCE_MAX_LOAD"] == "2.4"


def test_catalog_preparation_rejects_enabled_deletion(tmp_path, monkeypatch, configured):
    monkeypatch.setenv("RAW_ARCHIVE_DELETE_ENABLED", "true")
    with pytest.raises(ArchiveBlocked, match="cannot enable"):
        worker.export_source("gateway", tmp_path / "new")


class Box:
    identity = "independent-fixture-archive"

    def __init__(self, free=10**12):
        self.bodies, self.free = {}, free
        self.closed = False

    def session(self, _):
        return nullcontext()

    def capacity(self):
        return {"total_bytes": 10**12, "available_bytes": self.free}

    def publish_catalog(self, path, key):
        self.bodies[key] = path.read_bytes()

    def download_catalog(self, *a, **kw):
        pytest.fail("Verification must never use upload access")

    def close(self):
        self.closed = True


def publication(tmp_path, monkeypatch, configured, *, free=10**12, corrupt=False):
    root, manifest = fixture_bundle(tmp_path)
    catalog.save_json(root / "manifest.json", manifest)
    box, probe = Box(free), Probe(True)

    def read(key, target, limit, **_):
        body = box.bodies[key]
        assert len(body) <= limit
        target.write_bytes(body if not corrupt else b"incomplete")

    reader = SimpleNamespace(identity=box.identity, download_catalog=read, close=lambda: None)
    monkeypatch.setattr(worker, "health_probe", lambda: probe)
    monkeypatch.setattr(worker, "destination_from_environment", lambda: box)
    monkeypatch.setattr(
        "app.raw_archive.read_only_box.configured_catalog_read_only_box", lambda: reader
    )
    return root, manifest, box, probe


def test_complete_catalog_uses_independent_reader_and_empty_database(
    tmp_path, monkeypatch, configured
):
    root, manifest, box, probe = publication(tmp_path, monkeypatch, configured)
    output = tmp_path / "readback"
    proof = worker.publish_restore(root / "manifest.json", output)
    assert proof["tables"] == manifest["tables"]
    assert proof["independent_read_identity"] is True and proof["complete"] is True
    assert proof["deleted_files"] == 0 and proof["deletion_readiness"] is False
    assert (output / "wav-catalog.sqlite3").is_file()
    assert box.closed and probe.closed


def test_low_box_quota_uploads_nothing(tmp_path, monkeypatch, configured):
    root, _, box, _ = publication(tmp_path, monkeypatch, configured, free=1024)
    with pytest.raises(SafetyPause, match="storage_box_space"):
        worker.publish_restore(root / "manifest.json", tmp_path / "readback")
    assert box.bodies == {}


def test_corrupt_readback_cannot_claim_success(tmp_path, monkeypatch, configured):
    root, _, box, _ = publication(tmp_path, monkeypatch, configured, corrupt=True)
    output = tmp_path / "readback"
    with pytest.raises(ArchiveBlocked, match="readback mismatch"):
        worker.publish_restore(root / "manifest.json", output)
    assert not (output / "verified.json").exists()
    assert (root / "manifest.json").is_file() and box.bodies


def test_source_parts_and_previous_restore_are_never_replaced(tmp_path, monkeypatch, configured):
    root, _, box, _ = publication(tmp_path, monkeypatch, configured)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "proof").write_text("retain")
    with pytest.raises(ArchiveBlocked, match="new verification directory"):
        worker.publish_restore(root / "manifest.json", existing)
    assert (existing / "proof").read_text() == "retain" and box.bodies == {}
