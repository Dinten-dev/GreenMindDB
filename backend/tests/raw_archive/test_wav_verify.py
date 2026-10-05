"""Operator verification uses the correct account and pauses without archive operations."""

import json
import os
from types import SimpleNamespace

import pytest

from app.raw_archive import runner, wav_verify
from app.raw_archive.policy import ArchiveBlocked


def test_wrong_account_rejected_before_output_or_connections(tmp_path, monkeypatch):
    monkeypatch.setattr(
        wav_verify.accounts, "getpwnam", lambda _: SimpleNamespace(pw_uid=os.geteuid() + 1)
    )
    monkeypatch.setattr("sys.argv", ["wav_verify", "--output", str(tmp_path / "new")])
    with pytest.raises(ArchiveBlocked, match="account"):
        wav_verify.main()
    assert not (tmp_path / "new").exists()


def test_memory_pause_has_no_upload_or_readback(tmp_path, monkeypatch, capsys):
    class Probe:
        last = {"reason": "memory"}

        def __call__(self):
            return False

        def close(self):
            pass

    monkeypatch.setattr(
        wav_verify.accounts, "getpwnam", lambda _: SimpleNamespace(pw_uid=os.geteuid())
    )
    monkeypatch.setattr(runner, "health_probe", Probe)
    monkeypatch.setattr(
        runner,
        "destination_from_environment",
        lambda: pytest.fail("No archive connection during resource pause"),
    )
    monkeypatch.setattr("sys.argv", ["wav_verify", "--output", str(tmp_path / "new")])
    wav_verify.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "paused" and result["reason"] == "memory"
    assert result["deleted_files"] == 0 and result["metadata_published"] is False
    assert result["readbacks"] == []
