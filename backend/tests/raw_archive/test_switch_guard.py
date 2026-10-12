"""A failed nginx rehearsal restores exact original bytes, only on isolated fixtures."""

import hashlib
import importlib.util
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.raw_archive.compat_proxy import ANCHOR, draft


def test_failed_nginx_validation_restores_original_bytes(tmp_path, monkeypatch):
    source = (
        Path(__file__).resolve().parents[3]
        / "deploy/raw-archive/delete-preparation/switch-reader.py"
    )
    spec = importlib.util.spec_from_file_location("fixture_switch", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    revision = "a" * 40
    package = tmp_path / "package"
    package.mkdir()
    (package / "bundle.json").write_text(json.dumps({"revision": revision}))
    state = tmp_path / "state" / revision[:12] / "reader"
    state.mkdir(parents=True)
    before = "# preserved\n" + ANCHOR + "    }\n"

    def sha(text):
        return hashlib.sha256(text.encode()).hexdigest()

    candidate = draft(before, sha(before), reader_port=8141)
    for name, text in (("before.conf", before), ("candidate.conf", candidate)):
        (state / name).write_text(text)
    (state / "manifest.json").write_text(
        json.dumps(
            {"started": True, "before_sha256": sha(before), "candidate_sha256": sha(candidate)}
        )
    )
    target = tmp_path / "production.conf"
    target.write_text(before)
    proof = tmp_path / "acceptance.json"
    proof.write_text(
        json.dumps(
            {
                "revision": revision,
                "passed": True,
                "base": "http://127.0.0.1:8141",
                "at": datetime.now(UTC).isoformat(),
                "checks": [
                    "gateway_old_url_local",
                    "gateway_old_url_archive_only",
                    "direct_old_url_local",
                    "direct_old_url_archive_only",
                    "real_zone_denial",
                    "sha256_and_wav_decode",
                    "cookie_authentication",
                    "no_ingestion_routes",
                ],
            }
        )
    )
    proof.chmod(0o600)
    real_path = module.Path

    def paths(value):
        return (
            tmp_path / "state"
            if value == "/mnt/HC_Volume_106755700/greenmind-delete-preparation"
            else real_path(value)
        )

    monkeypatch.setattr(module, "Path", paths)
    monkeypatch.setattr(module, "RUNTIME", package)
    monkeypatch.setattr(module, "TARGET", target)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        module, "protected", lambda: {"receivers": "unchanged", "greenmind-prod": sha(before)}
    )

    class Probe:
        def __call__(self):
            return True

        def close(self):
            pass

    probe = Probe()
    monkeypatch.setattr(module, "health_probe", lambda: probe)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "switch-reader",
            "--acceptance",
            str(proof),
            "--acceptance-sha256",
            sha(proof.read_text()),
            "--rehearse",
        ],
    )
    with pytest.raises(subprocess.CalledProcessError):
        module.main()
    assert target.read_text() == before
    assert calls == [["nginx", "-t"], ["nginx", "-t"], ["systemctl", "reload", "nginx"]]
