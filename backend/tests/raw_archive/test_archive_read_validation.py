"""Independent samples never substitute for catalog or live reader acceptance."""

import hashlib
import importlib.util
import io
import json
import os
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.raw_archive.policy import Recording


def configured(tmp_path, monkeypatch, *, corrupt=False):
    path = Path(__file__).resolve().parents[3] / "deploy/archive-server/validate-archive-read.py"
    spec = importlib.util.spec_from_file_location("archive_read_validation", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.setattr(script, "STATE", tmp_path)
    monkeypatch.setattr(script.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=os.geteuid()))
    for key in list(os.environ):
        if key.startswith("RAW_ARCHIVE_SFTP_"):
            monkeypatch.delenv(key)
    output = tmp_path / "new"
    output.mkdir(mode=0o700)
    raw = io.BytesIO()
    with wave.open(raw, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(10)
        audio.writeframes(b"\x00\x01" * 10)
    wav = raw.getvalue()
    destination = "sftp://u676312@u676312.your-storagebox.de:23/greenmind-raw"
    requests = {}
    now = datetime.now(UTC)
    for kind in ("gateway", "direct"):
        record = Recording(
            kind,
            "sample",
            "greenmind-raw",
            kind + ".wav",
            hashlib.sha256(wav).hexdigest(),
            len(wav),
            now,
            now,
            "",
            now - timedelta(seconds=1),
            "mac-14-c1-9f-49-ab-c8",
        )
        requests[kind] = {
            "recording": json.loads(json.dumps(vars(record), default=str)),
            "destination": destination,
            "remote_key": record.remote_key("production"),
        }
    (output / "requests.json").write_text(json.dumps(requests))
    calls = []

    def download(key, target, limit):
        calls.append(key)
        assert limit == len(wav)
        target.write_bytes(wav[:-1] if corrupt else wav)

    box = SimpleNamespace(identity=destination, download=download, close=lambda: None)
    monkeypatch.setattr("app.raw_archive.read_only_box.configured_read_only_box", lambda: box)
    return script, output, calls


def test_good_readbacks_keep_all_remaining_acceptance_gates_false(tmp_path, monkeypatch):
    script, output, calls = configured(tmp_path, monkeypatch)
    script.worker(output)
    proof = json.loads((output / "verified.json").read_text())
    assert len(calls) == 2 and len(proof["readbacks"]) == 2
    assert all(row["full_decode"] for row in proof["readbacks"])
    for key in (
        "complete_catalog",
        "provider_snapshot_accepted",
        "reader_authorization_accepted",
        "deletion_readiness",
    ):
        assert proof[key] is False


def test_corrupt_archive_retains_evidence_without_success(tmp_path, monkeypatch):
    script, output, _ = configured(tmp_path, monkeypatch, corrupt=True)
    with pytest.raises(AssertionError):
        script.worker(output)
    assert (output / "gateway-archive.wav").exists()
    assert not (output / "verified.json").exists()


def test_upload_access_in_read_process_is_rejected_before_download(tmp_path, monkeypatch):
    script, output, calls = configured(tmp_path, monkeypatch)
    monkeypatch.setenv("RAW_ARCHIVE_SFTP_USER", "unexpected-upload-access")
    with pytest.raises(AssertionError):
        script.worker(output)
    assert not calls
