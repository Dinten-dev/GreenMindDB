"""Activate only an explicitly authorized private catalog backup in an existing preparation."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REVISION = "d344695ef260c2a38d2de5295e891e190487f591"
RUNTIME = Path("/opt/greenmind/delete-preparation") / REVISION[:12]
STATE = Path("/mnt/HC_Volume_106755700/greenmind-delete-preparation") / REVISION[:12]
ENVIRONMENT = Path("/etc/greenmind/delete-preparation") / (REVISION[:12] + ".env")
UNIT_DIRECTORY = Path("/etc/systemd/system")
DESTINATION = "u676312"


def units(service, timer, state, environment):
    """Transform reviewed templates; retain their CPU, RAM, disk and read-only guards."""
    authorization = state / "catalog-upload-authorization.json"
    report = state / "reports/catalog-published.json"
    expired = state / "reports/catalog-expired.json"
    original_condition = (
        "ConditionPathExists=/etc/greenmind/delete-preparation/CATALOG_BACKUP_APPROVED"
    )
    assert service.count(original_condition) == 1
    service = service.replace(
        original_condition,
        f"ConditionPathExists={authorization}\n"
        f"ConditionPathExists=!{report}\nConditionPathExists=!{expired}",
    )
    old_command = (
        "-m app.raw_archive.recovery --ledger /var/lib/greenmind-raw-copy/archive.sqlite3 "
        f"--output {state / 'backups'} --publish-catalog"
    )
    assert service.count(old_command) == 1
    service = service.replace(
        old_command,
        f"-m app.raw_archive.catalog_once --output {state / 'backups'} --report {report}",
    )
    assert f"EnvironmentFile={environment}" in service
    # This override raises only the new backup's guard, preserving reception headroom.
    service += (
        f"\nReadWritePaths={state / 'reports'}\nSuccessExitStatus=75\n"
        "Environment=RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB=640\n"
        "Environment=RAW_ARCHIVE_MAX_HOST_LOAD=2.4\n"
    )
    timer = timer.replace("FILL_REVISION", REVISION[:12])
    assert "FILL_" not in service + timer
    for value in (
        "MemoryMax=128M",
        "MemorySwapMax=0",
        "CPUQuota=10%",
        "NoNewPrivileges=true",
        "BindReadOnlyPaths=/var/lib/greenmind-raw-copy/archive.sqlite3",
        "UMask=0077",
    ):
        assert value in service
    assert "Unit=greenmind-catalog-backup-" + REVISION[:12] + ".service" in timer
    return service, timer


def private_new(path, data):
    with path.open("x") as body:
        os.chmod(path, 0o600)
        json.dump(data, body, indent=2)
        body.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authorize-private-catalog", action="store_true", required=True
    )
    parser.add_argument("--destination-user", choices=[DESTINATION], required=True)
    parser.add_argument("--script-sha256", required=True)
    parser.add_argument("--start-now", action="store_true")
    args = parser.parse_args()
    assert os.geteuid() == 0
    assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == args.script_sha256
    bundle = json.loads((RUNTIME / "bundle.json").read_text())
    assert bundle["revision"] == REVISION
    for name, digest in bundle["files"].items():
        path = RUNTIME / name
        assert not path.is_symlink() and path.stat().st_uid == 0
        assert path.stat().st_mode & 0o022 == 0
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    values = dict(
        line.split("=", 1)
        for line in ENVIRONMENT.read_text().splitlines()
        if line and not line.startswith("#")
    )
    assert values["RAW_ARCHIVE_SFTP_USER"] == args.destination_user
    assert values["RAW_ARCHIVE_SFTP_HOST"] == DESTINATION + ".your-storagebox.de"
    assert values["RAW_ARCHIVE_ENVIRONMENT"] == "production"
    for flag in (
        "RAW_ARCHIVE_ENABLED",
        "RAW_ARCHIVE_DELETE_ENABLED",
        "RAW_ARCHIVE_READS_ACCEPTED",
        "RETENTION_ENABLED",
        "DIRECT_RETENTION_ENABLED",
        "VISUAL_PRUNE_ENABLED",
    ):
        assert values[flag] == "false"
    assert datetime.now(UTC) < datetime.fromisoformat(
        values["RAW_ARCHIVE_PREPARATION_DEADLINE"]
    )
    sys.path.insert(0, str(RUNTIME / "backend"))
    from app.raw_archive.observation import protected

    before = protected()
    installation = json.loads((STATE / "installation.json").read_text())["protected"]
    assert all(before[k] == installation[k] for k in before if k in installation)
    assert before["greenmind-prod"] == installation["proxy_sha256"]
    assert before["greenmind-staging"] == installation["staging_sha256"]

    def timers():
        return {
            name: subprocess.check_output(
                ["systemctl", "is-active", name], text=True, timeout=5
            ).strip()
            for name in installation["timers"]
        }

    assert timers() == installation["timers"]
    service, timer = units(
        (STATE / "units/recovery.service.example").read_text(),
        (
            RUNTIME / "deploy/raw-archive/delete-preparation/catalog.timer.example"
        ).read_text(),
        STATE,
        ENVIRONMENT,
    )
    name = "greenmind-catalog-backup-" + REVISION[:12]
    paths = [UNIT_DIRECTORY / (name + "." + suffix) for suffix in ("service", "timer")]
    authorization = STATE / "catalog-upload-authorization.json"
    assert not authorization.exists() and all(not path.exists() for path in paths)
    private_new(
        authorization,
        {
            "authorized_at": datetime.now(UTC).isoformat(),
            "authorization": "Explicit human permission in the GreenMind chat on 2026-10-03",
            "payload": "Complete private recovery catalog including emails and password hashes",
            "destination_account": DESTINATION,
            "destination_path": values.get("RAW_ARCHIVE_SFTP_ROOT", "greenmind-raw")
            + "/production/catalog-backup",
            "script_sha256": args.script_sha256,
            "deletion_authorized": False,
            "expires_at": values["RAW_ARCHIVE_PREPARATION_DEADLINE"],
        },
    )
    for path, content in zip(paths, (service, timer), strict=True):
        with path.open("x") as body:
            os.chmod(path, 0o644)
            body.write(content)
    subprocess.run(
        ["systemd-analyze", "verify", *map(str, paths)], check=True, timeout=15
    )
    subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=15)
    assert protected() == before
    assert timers() == installation["timers"]
    subprocess.run(
        ["systemctl", "enable", "--now", name + ".timer"], check=True, timeout=15
    )
    if args.start_now:
        subprocess.run(
            ["systemctl", "start", "--no-block", name + ".service"],
            check=True,
            timeout=15,
        )
    assert protected() == before
    result = {
        "at": datetime.now(UTC).isoformat(),
        "revision": REVISION,
        "catalog_timer": name + ".timer",
        "immediate_attempt_requested": args.start_now,
        "publication_confirmed": False,
        "deletion_enabled": False,
        "protected_unchanged": True,
        "created_admin_identities": [],
        "existing_access_used": [
            "SSH traver",
            "sudo root",
            "greenmind_archive_reader_33fe395",
            DESTINATION,
        ],
    }
    assert timers() == installation["timers"]
    private_new(STATE / "catalog-activation.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
