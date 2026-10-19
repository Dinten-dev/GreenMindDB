"""Install a separate WAV-only preparation; no receiver, copier or proxy replacement."""

import argparse
import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0 or sha(PACKAGE / "bundle.json") != args.bundle_sha256:
        raise RuntimeError("Explicit root install with a pinned bundle is required")
    bundle = json.loads((PACKAGE / "bundle.json").read_text())
    revision = bundle["revision"]
    if (
        not re.fullmatch(r"[0-9a-f]{40}", revision)
        or bundle.get("delete_enabled") is not False
    ):
        raise RuntimeError("Immutable code revision and disabled deletion required")
    for name, digest in bundle["files"].items():
        path = PACKAGE / name
        if (
            path.is_symlink()
            or PACKAGE not in path.resolve().parents
            or sha(path) != digest
        ):
            raise RuntimeError("Prepared package differs from reviewed files")
    sys.path.insert(0, str(PACKAGE / "backend"))
    from app.raw_archive.observation import protected

    before = protected()
    account = pwd.getpwnam("greenmind-raw-copy")
    runtime = Path("/opt/greenmind/wav-metadata") / revision[:12]
    state = Path("/mnt/HC_Volume_106755700/greenmind-wav-metadata") / revision[:12]
    env = Path("/etc/greenmind/delete-preparation") / ("wav-" + revision[:12] + ".env")
    if runtime.exists() or state.exists() or env.exists():
        raise RuntimeError("Never overwrite an existing runtime or private evidence")
    runtime.mkdir(mode=0o755, parents=True)
    for name in bundle["files"]:
        target = runtime / name
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        shutil.copyfile(PACKAGE / name, target)
        os.chown(target, 0, 0)
        target.chmod(0o644)
    shutil.copyfile(PACKAGE / "bundle.json", runtime / "bundle.json")
    (runtime / "bundle.json").chmod(0o644)
    state.mkdir(mode=0o700, parents=True)
    os.chown(state, account.pw_uid, account.pw_gid)
    source = Path("/etc/greenmind/delete-preparation/d344695ef260.env")
    original = source.read_text()
    values = dict(
        line.split("=", 1)
        for line in original.splitlines()
        if line and not line.startswith("#")
    )
    disabled = (
        "RAW_ARCHIVE_ENABLED",
        "RAW_ARCHIVE_DELETE_ENABLED",
        "RAW_ARCHIVE_READS_ACCEPTED",
        "RETENTION_ENABLED",
        "DIRECT_RETENTION_ENABLED",
        "VISUAL_PRUNE_ENABLED",
    )
    if any(values.get(key, "").lower() != "false" for key in disabled):
        raise RuntimeError("Existing preparation has unexpected destructive flags")
    overrides = {key: "false" for key in disabled}
    overrides.update(
        RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB="640",
        RAW_ARCHIVE_MAX_HOST_LOAD="2.4",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(runtime / "backend"),
    )
    with env.open("x") as body:
        env.chmod(0o640)
        os.chown(env, 0, account.pw_gid)
        body.write(original.rstrip() + "\n")
        body.write(
            "\n".join(key + "=" + value for key, value in overrides.items()) + "\n"
        )
        body.flush()
        os.fsync(body.fileno())
    result = {
        "revision": revision,
        "runtime": str(runtime),
        "state": str(state),
        "environment_file": str(env),
        "installed_at": datetime.now(UTC).isoformat(),
        "deleted_files": 0,
        "admin_identities_created": [],
        "copy_code_changed": False,
        "proxy_changed": False,
        "scheduler_created": False,
    }
    if args.verify:
        output = state / ("verify-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f"))
        result["verification_unit"] = (
            "greenmind-wav-verify-" + revision[:12] + "-" + uuid.uuid4().hex[:6]
        )
        subprocess.run(
            [
                "/usr/bin/systemd-run",
                "--unit=" + result["verification_unit"],
                "--property=Type=oneshot",
                "--property=User=greenmind-raw-copy",
                "--property=Group=greenmind-raw-copy",
                "--property=MemoryMax=128M",
                "--property=MemorySwapMax=0",
                "--property=CPUQuota=10%",
                "--property=Nice=19",
                "--property=IOSchedulingClass=idle",
                "--property=TimeoutStartSec=180",
                "--property=TasksMax=32",
                "--property=NoNewPrivileges=yes",
                "--property=PrivateTmp=yes",
                "--property=ProtectSystem=strict",
                "--property=ProtectHome=read-only",
                "--property=UMask=0077",
                "--property=EnvironmentFile=" + str(env),
                "--property=ReadWritePaths="
                + str(state)
                + " /var/lib/greenmind-raw-copy",
                "--property=BindReadOnlyPaths=/var/lib/greenmind-raw-copy/archive.sqlite3",
                "/opt/greenmind/raw-copy/20260925/venv/bin/python",
                "-m",
                "app.raw_archive.wav_verify",
                "--output",
                str(output),
            ],
            check=True,
            timeout=10,
            capture_output=True,
        )
        result["verification_output"] = str(output)
    result["protected_unchanged"] = protected() == before
    if not result["protected_unchanged"]:
        raise RuntimeError(
            "Protected receiver/proxy baseline changed during preparation"
        )
    audit = state / "installation.json"
    with audit.open("x") as body:
        audit.chmod(0o600)
        json.dump(result, body, indent=2)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
