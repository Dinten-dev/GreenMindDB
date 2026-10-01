"""Future eviction safety checks, isolated from every live data source."""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.raw_archive.policy import ArchiveBlocked, Config, Ledger, Recording, archive_one, cutoff

NOW = datetime(2026, 9, 24, 20, tzinfo=UTC)
OLD = datetime(2026, 5, 1, tzinfo=UTC)
PAYLOAD = b'RIFF-original-byte-identical-wav-fixture'


class Source:
    def __init__(self):
        self.deleted = False
        self.reads = 0

    def snapshot(self, recording):
        return {'VersionId': 'immutable-v1'}

    def download(self, recording, snapshot, path):
        self.reads += 1
        assert not self.deleted
        path.write_bytes(PAYLOAD)

    def evict(self, recording, snapshot):
        assert snapshot == {'VersionId': 'immutable-v1'}
        self.deleted = True


class Destination:
    identity = 'sftp://test-box/archive'

    def __init__(self):
        self.data = None
        self.corrupt = False
        self.unavailable = False

    def publish(self, path, key):
        self.data = path.read_bytes()

    def download(self, key, path, max_bytes):
        if self.unavailable:
            raise OSError('offline')
        path.write_bytes(b'corrupt' if self.corrupt else self.data)


@pytest.fixture
def setup(tmp_path):
    config = Config(enabled=True, root=tmp_path, min_free_bytes=0)
    recording = Recording('gateway', 'wav-id', 'greenmind-raw', 'sensor.wav',
                          hashlib.sha256(PAYLOAD).hexdigest(), len(PAYLOAD),
                          OLD, OLD, 'a' * 64, OLD, 'mac-14-c1-9f-d9-42-a4')
    journal = Ledger(tmp_path)
    yield config, recording, Source(), Destination(), journal
    journal.close()


def call(config, recording, source, remote, ledger, revalidate=lambda _: None):
    return archive_one(config, recording, source, remote, ledger, revalidate, now=lambda: NOW)


def test_default_is_inert(setup):
    config, recording, source, remote, ledger = setup
    with pytest.raises(ArchiveBlocked, match='disabled'):
        call(replace(config, enabled=False), recording, source, remote, ledger)
    assert source.reads == 0 and not source.deleted and remote.data is None


def test_swiss_calendar_day_replaces_three_month_wait(setup):
    config, recording, source, remote, ledger = setup
    assert cutoff(datetime(2026, 5, 31, tzinfo=UTC)) == datetime(2026, 5, 30, 22, tzinfo=UTC)
    with pytest.raises(ArchiveBlocked):
        call(config, replace(recording, ended_at=cutoff(NOW) + timedelta(seconds=1)),
             source, remote, ledger)
    with pytest.raises(ArchiveBlocked):
        call(config, replace(recording, received_at=NOW), source, remote, ledger)
    assert source.reads == 0
    recent = replace(recording, started_at=NOW - timedelta(days=1, hours=2),
                     ended_at=NOW - timedelta(days=1), received_at=NOW - timedelta(days=1))
    assert call(replace(config, delete_enabled=True, reads_accepted=True),
                recent, source, remote, ledger) == 'evicted'
    assert source.deleted


def test_copy_only_never_deletes(setup):
    config, recording, source, remote, ledger = setup
    assert call(config, recording, source, remote, ledger) == 'verified'
    assert not source.deleted and remote.data == PAYLOAD
    assert ledger.load(recording)[0] == 'verified'


@pytest.mark.parametrize('failure', ['corrupt', 'unavailable'])
def test_bad_remote_retains_original(setup, failure):
    config, recording, source, remote, ledger = setup
    setattr(remote, failure, True)
    with pytest.raises((ArchiveBlocked, OSError)):
        call(replace(config, delete_enabled=True, reads_accepted=True),
             recording, source, remote, ledger)
    assert not source.deleted
    assert ledger.load(recording) is None


def test_read_integration_gate_blocks_deletion(setup):
    config, recording, source, remote, ledger = setup
    with pytest.raises(ArchiveBlocked, match='separate acceptance'):
        call(replace(config, delete_enabled=True), recording, source, remote, ledger)
    assert not source.deleted and ledger.load(recording)[0] == 'verified'


def test_features_rechecked_before_delete(setup):
    config, recording, source, remote, ledger = setup
    checks = []

    def revalidate(_):
        checks.append(True)
        if len(checks) == 2:
            raise ArchiveBlocked('features changed')

    with pytest.raises(ArchiveBlocked, match='features changed'):
        call(replace(config, delete_enabled=True, reads_accepted=True),
             recording, source, remote, ledger, revalidate)
    assert not source.deleted


def test_resume_after_copy_reads_remote_again(setup):
    config, recording, source, remote, ledger = setup
    call(config, recording, source, remote, ledger)
    remote.corrupt = True
    with pytest.raises(ArchiveBlocked):
        call(replace(config, delete_enabled=True, reads_accepted=True),
             recording, source, remote, ledger)
    assert source.reads == 1 and not source.deleted


def test_durable_delete_receipt_and_restart(setup):
    config, recording, source, remote, ledger = setup
    real_evict = source.evict

    def evict(*args):
        assert ledger.load(recording)[0] == 'deleting'
        real_evict(*args)
        raise OSError('process failed after exact version removal')

    source.evict = evict
    config = replace(config, delete_enabled=True, reads_accepted=True)
    with pytest.raises(OSError):
        call(config, recording, source, remote, ledger)
    assert source.deleted
    source.evict = real_evict
    assert call(config, recording, source, remote, ledger) == 'evicted'
    assert source.reads == 1


def test_older_retention_cannot_bypass_policy(setup, monkeypatch):
    config, recording, source, remote, ledger = setup
    monkeypatch.setenv('DIRECT_RETENTION_ENABLED', 'true')
    with pytest.raises(ArchiveBlocked, match='retention'):
        call(replace(config, delete_enabled=True, reads_accepted=True),
             recording, source, remote, ledger)
    assert not source.deleted
