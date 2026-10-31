"""Actual hooks with a small transaction boundary double; never Production SQL."""

import hashlib
import io
import json
from contextlib import contextmanager
from contextvars import copy_context
from types import SimpleNamespace

import pytest

from app.direct.config import DirectSettings
from app.direct.storage import ArtifactStore
from app.raw_archive import reader, worklease
from app.raw_archive.policy import ArchiveBlocked
from app.services import wav_feature_service, wav_service


class Transactions:
    def __init__(self):
        self.active = 0
        self.connections = 0
        self.writer = False

    @contextmanager
    def connect(self):
        self.connections += 1
        state = self

        class Connection:
            acquired = False

            @contextmanager
            def begin(self):
                try:
                    yield
                finally:
                    if self.acquired:
                        state.active -= 1

            def execute(self, statement, parameters=None):
                if str(statement) == "SET TRANSACTION READ ONLY":
                    return None
                assert str(statement) == "SELECT pg_try_advisory_xact_lock_shared(:key)"
                assert type(parameters["key"]) is int
                self.acquired = not state.writer
                state.active += int(self.acquired)
                return SimpleNamespace(scalar_one=lambda: self.acquired)

        yield Connection()


@pytest.fixture
def lease(monkeypatch):
    state = Transactions()
    monkeypatch.setenv("WAV_SOURCE_WORK_LEASES_ENABLED", "true")
    monkeypatch.setattr(worklease, "_source_engine", lambda _kind: state)
    return state


def test_exact_signed_key_and_shared_body_lifetime_across_stream_contexts(lease):
    identity = ["gateway", "greenmind-raw", "one.wav"]
    expected = int.from_bytes(
        hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).digest()[:8],
        "big",
        signed=True,
    )
    assert worklease.object_lock_key(*identity) == expected

    class Source:
        calls = 0

        def get_object(self, **_kwargs):
            self.calls += 1
            assert lease.active == 1
            return {"Body": io.BytesIO(b"synthetic-wave")}

    source = Source()
    response = reader.get_object(source, Bucket="greenmind-raw", Key="one.wav", kind="gateway")
    assert lease.active == 1
    assert response["Body"].read() == b"synthetic-wave"
    assert lease.active == 1  # EOF alone does not drop the active body lease.
    copy_context().run(response["Body"].close)
    response["Body"].close()
    assert lease.active == 0 and response["Body"].body.closed
    lease.writer = True
    with pytest.raises(ArchiveBlocked, match="busy"):
        reader.get_object(source, Bucket="greenmind-raw", Key="one.wav", kind="gateway")
    assert source.calls == 1 and lease.active == 0


def test_nested_feature_job_holds_one_lease_until_result_or_failure(lease, monkeypatch):
    record = SimpleNamespace(s3_key="one.wav")
    marker = object()

    def calculate(_db, _wav):
        assert lease.active == 1
        with worklease.shared_wav_lease("gateway", "greenmind-raw", "one.wav"):
            assert lease.active == 1 and lease.connections == 1
        return marker

    monkeypatch.setattr(wav_feature_service, "_extract_and_verify_wav_features", calculate)
    assert wav_feature_service.extract_and_verify_wav_features(None, record) is marker
    assert lease.active == 0

    def failed(_db, _wav):
        assert lease.active == 1
        raise RuntimeError("synthetic-feature-error")

    monkeypatch.setattr(wav_feature_service, "_extract_and_verify_wav_features", failed)
    with pytest.raises(RuntimeError, match="synthetic-feature"):
        wav_feature_service.extract_and_verify_wav_features(None, record)
    assert lease.active == 0


def test_direct_local_read_is_leased_and_untracked_urls_are_blocked(lease, monkeypatch, tmp_path):
    store = ArtifactStore(
        DirectSettings(artifact_root=tmp_path, s3_bucket="greenmind-direct-production")
    )
    key = "direct/" + "a" * 36 + "/" + "b" * 36 + "/" + "c" * 64 + ".wav"
    path = store._path(key)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"synthetic-local-wave")
    old = store._get_local

    def read(wanted):
        assert lease.active == 1
        return old(wanted)

    monkeypatch.setattr(store, "_get_local", read)
    assert store.get(key) == b"synthetic-local-wave"
    assert lease.active == 0 and lease.connections == 1
    with pytest.raises(ArchiveBlocked, match="authenticated leased"):
        wav_service.generate_presigned_url("one.wav")
