"""Opt-in real loopback SSH transport tests, using only disposable test files."""
import hashlib
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from app.raw_archive.policy import ArchiveBlocked, RemoteMissing, Ledger, archive_one
from app.raw_archive.storage import StorageBox
from app.raw_archive.telemetry import Metrics


@pytest.fixture
def box():
    path = os.environ.get('RAW_ARCHIVE_TEST_SSH_ROOT')
    if not path:
        pytest.skip('Disposable loopback SSH fixture is not configured')
    root = Path(path)
    return StorageBox(host='127.0.0.1', user='archive', port=55440,
                      key=root / 'key', known_hosts=root / 'known_hosts',
                      root='test-' + uuid4().hex)


def key_for(payload):
    return 'staging/gateway/' + 'a' * 64 + '/' + hashlib.sha256(payload).hexdigest() + '.wav'


def test_mux_roundtrip_reuses_one_master_and_cleans_up(box):
    metrics = Metrics()
    with tempfile.TemporaryDirectory(dir='/tmp', prefix='gm-mux-') as folder:
        root = Path(folder)
        with box.session(root, metrics=metrics):
            for index in range(3):
                data = bytes([index]) * 4096
                source = root / f'{index}.wav'
                source.write_bytes(data)
                box.publish(source, key_for(data))
                first_master = box._master
                returned = root / f'{index}.returned'
                box.download(key_for(data), returned, len(data))
                assert returned.read_bytes() == data and box._master is first_master
            socket = Path(box._socket)
            assert socket.exists()
            assert socket.parent.stat().st_mode & 0o777 == 0o700
        assert first_master.poll() is not None and not socket.exists()
    assert metrics.counts['ssh_connect'] == 1
    assert metrics.counts['reused_sftp_sessions'] == 9


def test_dead_master_blocks_instead_of_reconnecting(box):
    with tempfile.TemporaryDirectory(dir='/tmp', prefix='gm-mux-') as folder:
        root = Path(folder)
        with box.session(root):
            with pytest.raises(RemoteMissing):
                box.download(key_for(b'missing'), root / 'absent', 7)
            master = box._master
            master.terminate()
            master.wait(timeout=3)
            with pytest.raises(ArchiveBlocked, match='transport lost'):
                box.download(key_for(b'next'), root / 'next', 4)
            assert box._master is master


def test_failed_master_start_cleans_private_socket_directory(box):
    box._ssh_command = ['/usr/bin/false']
    with tempfile.TemporaryDirectory(dir='/tmp', prefix='gm-mux-') as folder:
        root = Path(folder)
        with pytest.raises(ArchiveBlocked, match='could not start'):
            with box.session(root):
                box.download(key_for(b'x'), root / 'x', 1)
        assert list(root.iterdir()) == [] and box._master is None


def test_disconnect_after_upload_cannot_mark_verified_and_retry_is_safe(box, world):
    config, catalogs, source, _, add, _ = world
    rec = add('gateway', 42)
    original = box.publish
    def disconnect(path, key):
        original(path, key)
        box._master.terminate()
        box._master.wait(timeout=3)
    ledger = Ledger(config.root)
    try:
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='gm-mux-') as folder:
            box.publish = disconnect
            with pytest.raises(ArchiveBlocked, match='transport lost'):
                with box.session(Path(folder)):
                    archive_one(config, rec, source, box, ledger,
                                catalogs['gateway'].revalidate, copy_only=True)
            assert ledger.load(rec) is None
            assert rec.key in source.data
            box.publish = original
            with box.session(Path(folder)):
                assert archive_one(config, rec, source, box, ledger,
                    catalogs['gateway'].revalidate, copy_only=True) == 'verified'
            assert ledger.load(rec)[0] == 'verified' and rec.key in source.data
    finally:
        ledger.close()
