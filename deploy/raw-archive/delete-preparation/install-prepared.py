"""Install an inert, immutable package. Never starts services or changes live routes."""

import hashlib
import argparse
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

PACKAGE = Path(__file__).resolve().parents[3]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_env(path):
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def protected():
    names = [
        "gm-zones-production-2e687bec010a-application-1",
        "gm-direct-production-direct-api-1",
        "greenminddb-backend-1",
    ]
    result = {}
    for name in names:
        result[name] = json.loads(
            subprocess.check_output(
                [
                    "docker",
                    "inspect",
                    "--format",
                    '{"running":{{.State.Running}},"started":{{json .State.StartedAt}},"restarts":{{.RestartCount}}}',
                    name,
                ],
                text=True,
                timeout=10,
            )
        )
        assert result[name]["running"]
    result["proxy_sha256"] = digest(Path("/etc/nginx/sites-available/greenmind-prod"))
    result["staging_sha256"] = digest(
        Path("/etc/nginx/sites-available/greenmind-staging")
    )
    result["timers"] = {
        name: subprocess.check_output(
            ["systemctl", "is-active", name], text=True, timeout=5
        ).strip()
        for name in (
            "greenmind-raw-copy.timer",
            "greenmind-raw-copy-drain.timer",
            "greenmind-archive-export.timer",
        )
    }
    for port in (8120, 8003, 8000):
        subprocess.run(
            [
                "curl",
                "--max-time",
                "3",
                "-fsS",
                "-o",
                "/dev/null",
                f"http://127.0.0.1:{port}/health",
            ],
            check=True,
            timeout=5,
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--activate-copy-code",
        action="store_true",
        help="Use new copy-only code on the next scheduled run; never restarts receivers",
    )
    args = parser.parse_args()
    assert os.geteuid() == 0, "Root installs only the separate private preparation"
    bundle = json.loads((PACKAGE / "bundle.json").read_text())
    revision = bundle["revision"]
    assert len(revision) == 40 and all(c in "0123456789abcdef" for c in revision)
    for relative, sha in bundle["files"].items():
        path = PACKAGE / relative
        assert (
            not path.is_symlink()
            and path.is_file()
            and PACKAGE in path.resolve().parents
        )
        assert digest(path) == sha
    runtime = Path("/opt/greenmind/delete-preparation") / revision[:12]
    state = (
        Path("/mnt/HC_Volume_106755700/greenmind-delete-preparation") / revision[:12]
    )
    env_path = Path("/etc/greenmind/delete-preparation") / (revision[:12] + ".env")
    assert not runtime.exists() and not state.exists() and not env_path.exists()
    before = protected()
    runtime.mkdir(parents=True, mode=0o755)
    shutil.copytree(PACKAGE / "backend", runtime / "backend")
    shutil.copytree(PACKAGE / "deploy", runtime / "deploy")
    shutil.copy2(PACKAGE / "bundle.json", runtime / "bundle.json")
    for name in ("adaptive-copy.py", "optimized.py"):
        shutil.copy2(PACKAGE / "deploy/raw-archive" / name, runtime / name)
    for path in runtime.rglob("*"):
        assert not path.is_symlink()
        os.chown(path, 0, 0)
        path.chmod(0o755 if path.is_dir() else 0o644)
    state.mkdir(parents=True, mode=0o700)
    os.chown(state, 996, 986)
    for name in ("backups", "reports", "units"):
        folder = state / name
        folder.mkdir(mode=0o700)
        os.chown(folder, 996, 986)
    # Explicitly provided existing private configurations; no credential probing
    # of MinIO or receiving container environments.
    values = read_env(Path("/etc/greenmind/raw-copy/storagebox.env"))
    reader = read_env(
        Path("/home/traver/greenmind-archive-production/33fe395/read.env")
    )
    for kind, key in (("GATEWAY", "DATABASE_URL"), ("DIRECT", "DIRECT_DATABASE_URL")):
        uri = urlsplit(reader[key])
        assert uri.hostname == "postgres" and uri.username.startswith(
            "greenmind_archive_reader_"
        )
        address = uri.netloc.rsplit("@", 1)[0] + "@127.0.0.1:5432"
        values["RAW_ARCHIVE_" + kind + "_DATABASE_URL"] = urlunsplit(
            (uri.scheme, address, uri.path, uri.query, "")
        )
    values.update(
        RAW_ARCHIVE_ENABLED="false",
        RAW_ARCHIVE_DELETE_ENABLED="false",
        RAW_ARCHIVE_READS_ACCEPTED="false",
        RAW_ARCHIVE_LEGACY_NULL_ACCEPTED="false",
        RAW_ARCHIVE_LOCAL_GRACE_DAYS="7",
        RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB="512",
        RAW_ARCHIVE_DELETION_APPROVAL_FILE="",
        RAW_ARCHIVE_DELETION_APPROVAL_SHA256="",
        RAW_ARCHIVE_MAX_HOST_LOAD="2.4",
        RETENTION_ENABLED="false",
        DIRECT_RETENTION_ENABLED="false",
        VISUAL_PRUNE_ENABLED="false",
        ARCHIVE_COMPAT_READS_ENABLED="false",
        RAW_ARCHIVE_ENVIRONMENT="production",
        RAW_ARCHIVE_HEALTH_URLS=",".join(
            f"http://127.0.0.1:{p}/health" for p in (8120, 8003, 8000)
        ),
        PYTHONPATH=str(runtime / "backend"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    assert all("\n" not in value and "\r" not in value for value in values.values())
    env_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with env_path.open("x") as body:
        os.chmod(env_path, 0o600)
        body.write(
            "".join(key + "=" + value + "\n" for key, value in sorted(values.items()))
        )
    templates = runtime / "deploy/raw-archive/delete-preparation"
    for name in ("preflight", "recovery"):
        content = (templates / (name + ".service.example")).read_text()
        content = content.replace(
            "/etc/greenmind/delete-preparation/recovery.env", str(env_path)
        )
        content = content.replace("FILL_IMMUTABLE_PACKAGE", str(runtime))
        content = content.replace(
            "FILL_PRIVATE_REPORT_DIRECTORY", str(state / "reports")
        )
        content = content.replace(
            "FILL_PRIVATE_BACKUP_DIRECTORY", str(state / "backups")
        )
        (state / "units" / (name + ".service.example")).write_text(content)
    if args.activate_copy_code:
        override = Path(
            "/etc/systemd/system/greenmind-raw-copy.service.d/90-first-verification.conf"
        )
        assert not override.exists()
        override.parent.mkdir(parents=True, exist_ok=True)
        with override.open("x") as body:
            body.write(
                "[Service]\nExecStart=\nExecStart=/opt/greenmind/raw-copy/20260925/venv/bin/python "
                + str(runtime / "optimized.py")
                + "\n"
            )
        subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=15)
        # Do not start/restart the copier. The unchanged existing timer will
        # select the new immutable code at its next ordinary scheduled run.
    assert before == protected(), "Live services, routes or schedules changed"
    result = {
        "revision": revision,
        "runtime": str(runtime),
        "state": str(state),
        "environment_file": str(env_path),
        "at": datetime.now(UTC).isoformat(),
        "services_started": 0,
        "deleted_files": 0,
        "deletion_enabled": False,
        "copy_code_next_scheduled_run": args.activate_copy_code,
        "protected": before,
    }
    (state / "installation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
