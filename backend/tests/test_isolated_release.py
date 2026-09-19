"""Exercise actual promotion, failure rollback, proxy preservation and release gates."""

import importlib.util
import json
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/release/release.py"
pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="requires repository operator package")


@pytest.fixture
def release():
    spec = importlib.util.spec_from_file_location("isolated_release", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_proxy_preserves_ingest_login_websocket_and_storage(release, environment):
    name = "green-mind.ch" if environment == "production" else "test.green-mind.ch"
    original = (ROOT / f"nginx/{name}.conf").read_text()
    _, _, _, api, front, old = release.CONFIG[environment]
    candidate, previous = release.proposal(original, environment, api, front)
    assert previous == old
    # Byte-for-byte retention of the old catch-all API including WebSocket headers.
    api_block = original.split("    # API → Backend directly")[1].split("    # Frontend (Next.js")[
        0
    ]
    assert api_block in candidate
    assert candidate.startswith(original.split("    # API → Backend directly")[0])
    assert original[original.rfind("\nserver {") :] in candidate
    assert f"proxy_pass http://127.0.0.1:{front};" in candidate
    assert f"proxy_pass http://127.0.0.1:{old};" in candidate
    assert "error_page 404 = @visual_previous_assets" in candidate
    next_candidate, previous = release.proposal(candidate, environment, api + 2, front + 2)
    assert previous == front
    assert next_candidate.count("location ^~ /api/v1/visualization/") == 1
    assert api_block in next_candidate
    third, previous = release.proposal(next_candidate, environment, api + 4, front + 4)
    assert previous == front + 2
    assert api_block in third


def test_no_mutation_for_unreviewed_full_stack_deploy(tmp_path):
    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/deploy.sh"), "--env", "production"],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--allow-receiver-restart" in result.stderr


def test_compose_has_no_receiving_services_or_pruning(release, tmp_path):
    manifest = {
        "environment": "production",
        "backend_image": "sha256:" + "a" * 64,
        "frontend_image": "sha256:" + "b" * 64,
        "api_port": 8004,
        "frontend_port": 3004,
        "project": "gm-release-production-test",
        "directory": str(tmp_path),
    }
    config = release.make_compose(manifest, {}, 1000, 1000)
    assert set(config["services"]) == {"api", "frontend", "worker", "direct-worker"}
    assert config["networks"]["existing"]["name"] == "greenminddb_default"
    assert all(s["image"].startswith("sha256:") for s in config["services"].values())
    assert "--prune" not in config["services"]["worker"]["command"]
    assert "app.direct.worker" not in json.dumps(config)
    assert config["services"]["worker"]["profiles"] == ["workers"]
    assert config["services"]["api"]["ports"] == ["127.0.0.1:8004:8000"]
    assert config["services"]["api"]["extra_hosts"] == ["green-mind.ch:172.28.20.1"]
    assert len(config["services"]["frontend"]["tmpfs"]) == 2
    assert len(config["services"]["api"]["tmpfs"]) == 1


@pytest.fixture
def prepared(release, tmp_path, monkeypatch):
    target = tmp_path / "installed.conf"
    target.write_text("old proxy")
    (tmp_path / "nginx.before.conf").write_text("old proxy")
    (tmp_path / "nginx.proposed.conf").write_text("new proxy")
    manifest = {
        "environment": "staging",
        "before_sha256": release.digest(target),
        "after_sha256": release.digest(tmp_path / "nginx.proposed.conf"),
        "protected": {},
        "previous_visual": False,
    }
    release.write_json(tmp_path / "manifest.json", manifest)
    monkeypatch.setattr(release.os, "geteuid", lambda: 0)
    monkeypatch.setattr(release, "check_previous", lambda *_: None)
    monkeypatch.setattr(release, "unchanged", lambda _: None)
    monkeypatch.setattr(release, "check", lambda *_: {"passed": True})
    monkeypatch.setattr(release, "run", lambda *_: "")
    monkeypatch.setattr(release, "compose", lambda *_: "{}")
    return tmp_path, target, manifest


def test_activation_switches_only_after_validation(release, prepared, monkeypatch):
    root, target, manifest = prepared
    events = []
    monkeypatch.setattr(release, "check", lambda *_: events.append(("verify", target.read_text())))
    monkeypatch.setattr(release, "run", lambda *args: events.append(args))
    release.activate_release(root, manifest, target)
    assert events[0] == ("verify", "old proxy")
    assert events[1:3] == [("nginx", "-t"), ("systemctl", "reload", "nginx")]
    assert target.read_text() == "new proxy"
    assert (root / "activated.json").exists()


@pytest.mark.parametrize("where", ["nginx", "public", "receivers"])
def test_failed_switch_restores_previous_proxy(release, prepared, monkeypatch, where):
    root, target, manifest = prepared
    calls = []

    def execute(*args):
        calls.append(args)
        if where == "nginx" and len(calls) == 1:
            raise RuntimeError("invalid candidate")
        return ""

    monkeypatch.setattr(release, "run", execute)
    if where == "public":
        monkeypatch.setattr(
            release,
            "compose",
            lambda *_: (_ for _ in ()).throw(RuntimeError("public check failed")),
        )
    if where == "receivers":
        monkeypatch.setattr(
            release, "unchanged", lambda *_: (_ for _ in ()).throw(RuntimeError("receiver changed"))
        )
    with pytest.raises(RuntimeError):
        release.activate_release(root, manifest, target)
    assert target.read_text() == "old proxy"
    assert calls[-2:] == [("nginx", "-t"), ("systemctl", "reload", "nginx")]
    assert not (root / "activated.json").exists()


def test_changed_installed_proxy_never_overwritten(release, prepared):
    root, target, manifest = prepared
    target.write_text("new unrelated operator change")
    with pytest.raises(RuntimeError, match="Active configuration changed"):
        release.activate_release(root, manifest, target)
    assert target.read_text() == "new unrelated operator change"


def test_production_activation_requires_real_staging_acceptance(release, prepared):
    root, target, manifest = prepared
    manifest["environment"] = "production"
    with pytest.raises(RuntimeError, match="Staging acceptance"):
        release.activate_release(root, manifest, target)
    assert target.read_text() == "old proxy"


def test_acceptance_pins_commit_images_hardware_and_age(release, tmp_path):
    manifest = {
        "revision": "a" * 40,
        "backend_image": "sha256:" + "b" * 64,
        "frontend_image": "sha256:" + "c" * 64,
    }
    report = tmp_path / "acceptance.json"
    evidence = manifest | {
        "environment": "staging",
        "checked_at": time.time(),
        "gateway_direct_parallel": True,
        "wav_sample_integrity": True,
        "interruption_recovery": True,
        "rollback_rehearsal": True,
        "ui_review": True,
    }
    release.write_json(report, evidence)
    release.acceptance(report, manifest)
    for patch in (
        {"revision": "d" * 40},
        {"backend_image": "wrong"},
        {"gateway_direct_parallel": False},
        {"checked_at": time.time() - 8 * 86400},
    ):
        release.write_json(report, evidence | patch)
        with pytest.raises(RuntimeError):
            release.acceptance(report, manifest)


def test_compacted_history_blocks_legacy_rollback(release, prepared, monkeypatch):
    root, target, manifest = prepared
    target.write_text("new proxy")
    monkeypatch.setattr(release, "compose", lambda *_: '{"compacted_chunks": 1}')
    with pytest.raises(RuntimeError, match="Restore compacted SQL"):
        release.rollback_release(root, manifest, target)
    assert target.read_text() == "new proxy"
    manifest["previous_visual"] = True
    release.rollback_release(root, manifest, target)
    assert target.read_text() == "old proxy"


def test_configuration_tampering_is_rejected(release, tmp_path):
    path = tmp_path / "runtime.env"
    path.write_text("ENVIRONMENT=production\n")
    (tmp_path / "compose.json").write_text("{}")
    manifest = {
        "artifacts": {
            name: release.digest(tmp_path / name) for name in ("runtime.env", "compose.json")
        }
    }
    release.verify_artifacts(tmp_path, manifest)
    path.write_text("ENVIRONMENT=staging\n")
    with pytest.raises(RuntimeError, match="configuration changed"):
        release.verify_artifacts(tmp_path, manifest)


def test_visualization_keeps_deployed_auth_guards_without_email_secret(monkeypatch):
    from app.config import Settings

    common = dict(
        _env_file=None,
        environment="production",
        service_role="visualization",
        jwt_secret_key="production-valid-secret-" * 3,
        cookie_secure=True,
        s3_access_key_id="restricted-bucket-access",
        s3_secret_access_key="a-real-restricted-storage-secret",
        frontend_url="https://green-mind.ch",
        cors_origins="https://green-mind.ch",
    )
    assert Settings(**common).service_role == "visualization"
    for patch in (
        {"cookie_secure": False},
        {"jwt_secret_key": "short"},
        {"cors_origins": "*"},
        {"s3_access_key_id": "minioadmin"},
        {"service_role": "application"},
    ):
        with pytest.raises(ValueError):
            Settings(**(common | patch))


def test_read_role_cannot_launch_the_receiving_app():
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.config import settings; settings.service_role='visualization'; import app.main",
        ],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "visualization role must use app.visualization.api" in result.stderr


def test_existing_archive_mount_is_preserved(release, tmp_path):
    manifest = {
        "environment": "production",
        "backend_image": "sha256:" + "a" * 64,
        "frontend_image": "sha256:" + "b" * 64,
        "api_port": 8004,
        "frontend_port": 3004,
        "project": "gm-release-production-test",
        "directory": str(tmp_path),
        "archive_directory": "/home/traver/greenmind-visual-production/archive",
    }
    config = release.make_compose(manifest, {}, 1000, 1000)
    assert config["services"]["worker"]["volumes"] == [
        "/home/traver/greenmind-visual-production/archive:/archive"
    ]
