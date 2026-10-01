"""Prepared storage safety tests; never contacts the real Storage Box."""

from datetime import UTC, datetime

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.storage import S3Source, StorageBox


def test_unversioned_objects_cannot_be_deleted():
    source = S3Source(None)
    with pytest.raises(ArchiveBlocked, match='versioning'):
        source.evict(None, {'VersionId': 'null'})


def test_recently_replaced_object_cannot_be_deleted():
    source = S3Source(None)
    with pytest.raises(ArchiveBlocked, match='closed Swiss calendar day'):
        source.evict(None, {'VersionId': 'v1', 'LastModified': datetime.now(UTC).isoformat()})


def test_host_keys_are_mandatory(tmp_path):
    with pytest.raises(ArchiveBlocked, match='host keys'):
        StorageBox(host='example.test', user='test', key=tmp_path/'key',
                   known_hosts=tmp_path/'missing')


def test_path_injection_rejected(tmp_path):
    key = tmp_path/'key'
    known = tmp_path/'hosts'
    key.touch()
    known.touch()
    remote = StorageBox(host='example.test', user='test', key=key, known_hosts=known)
    with pytest.raises(ArchiveBlocked, match='Unsafe'):
        remote.path('../other-data.wav')
    assert '-oStrictHostKeyChecking=yes' in remote.command
    assert '-oBatchMode=yes' in remote.command
