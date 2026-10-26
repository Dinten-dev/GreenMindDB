import hashlib
import io
import wave
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.raw_archive.policy import ArchiveBlocked, Config, Recording
from app.raw_archive.rehearsal import verify_sample


@pytest.fixture
def readback(tmp_path):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(128)
        wav.writeframes(b"\x00\x00" * 128)
    payload = buffer.getvalue()
    old = datetime.now(UTC) - timedelta(days=10)
    config = Config(enabled=True, root=tmp_path, min_free_bytes=0)
    destination = SimpleNamespace(identity="test-box")
    destination.download = lambda key, path, maximum: path.write_bytes(payload)
    source = SimpleNamespace(download=lambda rec, snap, path: path.write_bytes(payload))
    selected = {}
    for kind in ("gateway", "direct"):
        record = Recording(
            kind,
            kind,
            kind,
            kind + ".wav",
            hashlib.sha256(payload).hexdigest(),
            len(payload),
            old,
            old,
            "a" * 64,
            old,
            "mac-14-c1-9f-d9-42-a4",
        )
        selected[kind] = (
            record,
            {
                "destination": destination.identity,
                "remote_key": record.remote_key(config.namespace),
                "snapshot": {},
            },
        )
    return config, selected, dict.fromkeys(selected, source), destination


def test_both_real_wav_bodies_are_read_and_decoded(readback):
    result = verify_sample(*readback, checkpoint=lambda: None)
    assert {row["kind"] for row in result} == {"gateway", "direct"}
    assert all(row["wav_decoded"] and row["sample_rate"] == 128 for row in result)


def test_corrupt_backup_cannot_report_success(readback):
    readback[-1].download = lambda key, path, maximum: path.write_bytes(b"corrupt")
    with pytest.raises(ArchiveBlocked, match="readback mismatch"):
        verify_sample(*readback, checkpoint=lambda: None)


def test_missing_original_cannot_report_success(readback):
    def missing(*_):
        raise FileNotFoundError

    readback[2]["gateway"].download = missing
    with pytest.raises(FileNotFoundError):
        verify_sample(*readback, checkpoint=lambda: None)
