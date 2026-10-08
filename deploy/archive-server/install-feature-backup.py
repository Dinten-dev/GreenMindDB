"""Install only scientific feature backup using existing configured narrow access."""

import hashlib
import json
import os
import pwd
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SOURCE = Path(__file__).resolve().parents[2]
RUNTIME = Path("/opt/greenmind/feature-backup/20261008")
STATE = Path("/mnt/HC_Volume_106755700/greenmind-feature-backup")
ENV = Path("/etc/greenmind/feature-backup/backup.env")
METADATA = Path(
    "/home/traver/greenmind-archive-acceptance-20261007/greenmind_wav_metadata_b4ae52d674aa/metadata.env"
)


def read_env(path):
    if path.is_symlink() or path.stat().st_mode & 0o007:
        raise RuntimeError("Private regular configured access required")
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    )


def main():
    assert os.geteuid() == 0
    assert not RUNTIME.exists() and not ENV.exists()
    account = pwd.getpwnam("greenmind-raw-copy")
    old = read_env(Path("/etc/greenmind/raw-copy/storagebox.env"))
    credentials = read_env(METADATA)
    values = {
        key: value for key, value in old.items() if key.startswith("RAW_ARCHIVE_SFTP_")
    }
    assert values["RAW_ARCHIVE_SFTP_USER"] == "u676312"
    assert values["RAW_ARCHIVE_SFTP_HOST"] == "u676312.your-storagebox.de"
    for kind in ("GATEWAY", "DIRECT"):
        key = "RAW_ARCHIVE_" + kind + "_DATABASE_URL"
        uri = urlsplit(credentials[key])
        assert (
            uri.hostname == "127.0.0.1"
            and uri.username == "greenmind_wav_metadata_b4ae52d674aa"
        )
        values[key] = urlunsplit(
            (
                uri.scheme,
                uri.netloc.rsplit("@", 1)[0] + "@127.0.0.1:5432",
                uri.path,
                "",
                "",
            )
        )
    values.update(
        RAW_ARCHIVE_DIRECT_S3_BUCKET="greenmind-direct-production-hotspot",
        RAW_ARCHIVE_ENVIRONMENT="production",
        RAW_ARCHIVE_COORDINATION_DIR="/var/lib/greenmind-archive-coordination",
        RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB="640",
        RAW_ARCHIVE_MAX_HOST_LOAD="2.4",
        RAW_ARCHIVE_HEALTH_URLS=",".join(
            f"http://127.0.0.1:{port}/health" for port in (8120, 8003, 8000)
        ),
        RAW_ARCHIVE_SOURCE_MOUNTS="/,/mnt/HC_Volume_106755700",
        RAW_ARCHIVE_ENABLED="false",
        RAW_ARCHIVE_DELETE_ENABLED="false",
        RAW_ARCHIVE_READS_ACCEPTED="false",
        RETENTION_ENABLED="false",
        DIRECT_RETENTION_ENABLED="false",
        VISUAL_PRUNE_ENABLED="false",
        PYTHONPATH=str(RUNTIME / "backend"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    assert Path("/mnt/HC_Volume_106755700").is_mount()
    shutil.copytree(SOURCE / "backend", RUNTIME / "backend")
    (RUNTIME / "deploy/archive-server").mkdir(parents=True)
    shutil.copyfile(
        SOURCE / "deploy/archive-server/feature-backup-daily.py",
        RUNTIME / "deploy/archive-server/feature-backup-daily.py",
    )
    hashes = {}
    for path in RUNTIME.rglob("*"):
        assert not path.is_symlink()
        os.chown(path, 0, 0)
        path.chmod(0o755 if path.is_dir() else 0o644)
        if path.is_file():
            hashes[str(path.relative_to(RUNTIME))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    (RUNTIME / "files.json").write_text(json.dumps(hashes, sort_keys=True))
    STATE.mkdir(mode=0o700)
    os.chown(STATE, account.pw_uid, account.pw_gid)
    ENV.parent.mkdir(mode=0o750, parents=True)
    os.chown(ENV.parent, 0, account.pw_gid)
    with ENV.open("x") as body:
        os.fchmod(body.fileno(), 0o640)
        os.fchown(body.fileno(), 0, account.pw_gid)
        body.write("".join(key + "=" + value + "\n" for key, value in values.items()))
    service = f"""[Unit]
Description=GreenMind scientific features backup (no deletion)
After=network-online.target
[Service]
Type=oneshot
User=greenmind-raw-copy
Group=greenmind-raw-copy
EnvironmentFile={ENV}
ExecStart=/opt/greenmind/raw-copy/20260925/venv/bin/python {RUNTIME}/deploy/archive-server/feature-backup-daily.py
MemoryMax=128M
MemorySwapMax=0
CPUQuota=10%
Nice=19
IOSchedulingClass=idle
TasksMax=32
TimeoutStartSec=270
KillMode=control-group
SuccessExitStatus=75
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
UMask=0077
ReadWritePaths={STATE} /var/lib/greenmind-archive-coordination
"""
    timer = """[Unit]
Description=Daily GreenMind features backup with three bounded retries
[Timer]
OnCalendar=*-*-* 00,01,02,03:00:00 Europe/Zurich
Persistent=true
AccuracySec=1min
Unit=greenmind-feature-backup.service
[Install]
WantedBy=timers.target
"""
    paths = []
    for suffix, data in (("service", service), ("timer", timer)):
        path = Path("/etc/systemd/system/greenmind-feature-backup." + suffix)
        with path.open("x") as body:
            os.fchmod(body.fileno(), 0o644)
            body.write(data)
        paths.append(str(path))
    subprocess.run(
        ["systemd-analyze", "verify", *paths],
        check=True,
        capture_output=True,
        timeout=15,
    )
    subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=15)
    subprocess.run(
        ["systemctl", "enable", "--now", "greenmind-feature-backup.timer"],
        check=True,
        timeout=15,
    )
    print(
        json.dumps(
            {
                "status": "installed",
                "timer": "greenmind-feature-backup.timer",
                "admin_identities_created": [],
                "deletion_enabled": False,
                "receivers_modified": False,
            }
        )
    )


if __name__ == "__main__":
    main()
