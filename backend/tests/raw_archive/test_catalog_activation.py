"""Operator activation preserves service limits and rejects a colliding mc binary."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[3] / "deploy/raw-archive/delete-preparation"


def module():
    spec = importlib.util.spec_from_file_location(
        "catalog_activation", DEPLOY / "activate-catalog.py"
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_existing_preparation_activation_keeps_isolation_and_expiration(tmp_path):
    activation = module()
    state = tmp_path / "state"
    env = tmp_path / "private.env"
    service = (DEPLOY / "recovery.service.example").read_text()
    service = service.replace("FILL_IMMUTABLE_PACKAGE", str(activation.RUNTIME))
    service = service.replace("FILL_PRIVATE_BACKUP_DIRECTORY", str(state / "backups"))
    service = service.replace("/etc/greenmind/delete-preparation/recovery.env", str(env))
    service, timer = activation.units(
        service, (DEPLOY / "catalog.timer.example").read_text(), state, env
    )
    assert "catalog_once" in service and "--publish-catalog" not in service
    assert f"ConditionPathExists={state / 'catalog-upload-authorization.json'}" in service
    assert "ConditionPathExists=!" in service and "catalog-expired.json" in service
    assert "RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB=640" in service
    assert "MemoryMax=128M" in service and "CPUQuota=10%" in service
    assert "MemorySwapMax=0" in service and "BindReadOnlyPaths=" in service
    assert "Persistent=false" in timer
    assert (
        "ExecStart=" in service
        and "delete" not in service.split("ExecStart=", 1)[1].splitlines()[0]
    )


def test_changed_template_fails_before_unit_creation(tmp_path):
    with pytest.raises(AssertionError):
        module().units("[Service]\nExecStart=arbitrary\n", "", tmp_path, tmp_path / "env")


@pytest.mark.parametrize(
    "change", [{"RAW_ARCHIVE_DELETE_ENABLED": "true"}, {"RAW_ARCHIVE_SFTP_USER": "another-account"}]
)
def test_unsafe_existing_environment_never_creates_authorization_or_units(
    tmp_path, monkeypatch, change
):
    activation = module()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "bundle.json").write_text(json.dumps({"revision": activation.REVISION, "files": {}}))
    values = {
        "RAW_ARCHIVE_SFTP_USER": "u676312",
        "RAW_ARCHIVE_SFTP_HOST": "u676312.your-storagebox.de",
        "RAW_ARCHIVE_ENVIRONMENT": "production",
        "RAW_ARCHIVE_ENABLED": "false",
        "RAW_ARCHIVE_DELETE_ENABLED": "false",
        "RAW_ARCHIVE_READS_ACCEPTED": "false",
        "RETENTION_ENABLED": "false",
        "DIRECT_RETENTION_ENABLED": "false",
        "VISUAL_PRUNE_ENABLED": "false",
    } | change
    env = tmp_path / "env"
    env.write_text("".join(key + "=" + value + "\n" for key, value in values.items()))
    state, unit_dir = tmp_path / "state", tmp_path / "units"
    monkeypatch.setattr(activation, "RUNTIME", runtime)
    monkeypatch.setattr(activation, "STATE", state)
    monkeypatch.setattr(activation, "ENVIRONMENT", env)
    monkeypatch.setattr(activation, "UNIT_DIRECTORY", unit_dir)
    monkeypatch.setattr(activation.os, "geteuid", lambda: 0)
    digest = hashlib.sha256((DEPLOY / "activate-catalog.py").read_bytes()).hexdigest()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "activate-catalog",
            "--authorize-private-catalog",
            "--destination-user",
            "u676312",
            "--script-sha256",
            digest,
        ],
    )
    with pytest.raises(AssertionError):
        activation.main()
    assert not state.exists() and not unit_dir.exists()


@pytest.mark.parametrize(
    "version,success",
    [("GNU Midnight Commander 4.8.30", False), ("mc version RELEASE.2026-01-01T00-00-00Z", True)],
)
def test_provision_requires_real_minio_client_before_admin_calls(tmp_path, version, success):
    client = tmp_path / "client"
    log = tmp_path / "calls"
    client.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then\n  printf "%s\\n" "$FAKE_VERSION"\nelif [ "$1" = "--json" ]; then\n  if [ "$3" = "user" ]; then code=XMinioAdminNoSuchUser; else code=XMinioAdminNoSuchPolicy; fi\n  printf \'{"status":"error","error":{"cause":{"error":{"Code":"%s"}}}}\\n\' "$code"\n  exit 1\nelse\n  printf "%s\\n" "$1 $2 $3" >> "$CALL_LOG"\nfi\n'
    )
    client.chmod(0o700)
    policy = tmp_path / "policy.json"
    data = json.loads((DEPLOY / "diagnostic-policy.json").read_text())
    data["Statement"][0]["Resource"][1] = "arn:aws:s3:::greenmind-direct-production-test"
    policy.write_text(json.dumps(data))
    env = os.environ | {
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        "GREENMIND_MINIO_CLIENT": str(client),
        "GREENMIND_DIAGNOSTIC_ACCESS": "greenmind-diagnostic-fixture",
        "GREENMIND_DIAGNOSTIC_SECRET": "isolated-fixture-secret",
        "GREENMIND_DIAGNOSTIC_DIRECT_BUCKET": "greenmind-direct-production-test",
        "FAKE_VERSION": version,
        "CALL_LOG": str(log),
    }
    result = subprocess.run(
        ["/bin/bash", str(DEPLOY / "provision-diagnostic.sh"), "operator", str(policy)],
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert (result.returncode == 0) == success
    if success:
        assert log.read_text().splitlines() == [
            "admin policy create",
            "admin user add",
            "admin policy attach",
        ]
    else:
        assert not log.exists()


@pytest.mark.parametrize("lookup", ["exists", "denied", "malformed", "policy_exists"])
def test_diagnostic_provision_never_overwrites_unknown_user_or_policy(tmp_path, lookup):
    client = tmp_path / "client"
    log = tmp_path / "writes"
    client.write_text("""#!/bin/sh
if [ "$1" = "--version" ]; then echo 'mc version RELEASE.2026-01-01T00-00-00Z'; exit 0; fi
if [ "$1" = "--json" ]; then
  if [ "$LOOKUP" = "malformed" ]; then echo invalid; exit 1; fi
  if [ "$LOOKUP" = "denied" ]; then echo '{"status":"error","error":{"Code":"AccessDenied"}}'; exit 1; fi
  if [ "$LOOKUP" = "exists" ] || [ "$3" = "policy" ]; then echo '{"status":"success"}'; exit 0; fi
  echo '{"status":"error","error":{"Code":"XMinioAdminNoSuchUser"}}'; exit 1
fi
echo mutation >> "$CALL_LOG"
""")
    client.chmod(0o700)
    env = os.environ | {
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        "GREENMIND_MINIO_CLIENT": str(client),
        "GREENMIND_DIAGNOSTIC_ACCESS": "greenmind-diagnostic-fixture",
        "GREENMIND_DIAGNOSTIC_SECRET": "isolated-fixture-secret",
        "GREENMIND_DIAGNOSTIC_DIRECT_BUCKET": "greenmind-direct-production-hotspot",
        "CALL_LOG": str(log),
        "LOOKUP": lookup,
    }
    result = subprocess.run(
        [
            "/bin/bash",
            str(DEPLOY / "provision-diagnostic.sh"),
            "operator",
            str(DEPLOY / "diagnostic-policy.production.json"),
        ],
        env=env,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert not log.exists()


def test_diagnostic_policy_refuses_another_direct_bucket(tmp_path):
    client = tmp_path / "client"
    log = tmp_path / "calls"
    client.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "mc version RELEASE.2026-01-01T00-00-00Z"; else echo called >> "$CALL_LOG"; fi\n'
    )
    client.chmod(0o700)
    env = os.environ | {
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        "GREENMIND_MINIO_CLIENT": str(client),
        "GREENMIND_DIAGNOSTIC_ACCESS": "greenmind-diagnostic-fixture",
        "GREENMIND_DIAGNOSTIC_SECRET": "isolated-fixture-secret",
        "GREENMIND_DIAGNOSTIC_DIRECT_BUCKET": "greenmind-direct-production-other",
        "CALL_LOG": str(log),
    }
    result = subprocess.run(
        [
            "/bin/bash",
            str(DEPLOY / "provision-diagnostic.sh"),
            "operator",
            str(DEPLOY / "diagnostic-policy.production.json"),
        ],
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert not log.exists()
