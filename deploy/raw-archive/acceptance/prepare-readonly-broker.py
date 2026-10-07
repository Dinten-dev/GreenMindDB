"""Prepare replacement private broker credentials; activation remains a guarded step."""

import hashlib
import json
import os
import pwd
from pathlib import Path

REVISION = "b4ae52d674aa"
RUNTIME = Path("/opt/greenmind/archive-acceptance") / REVISION
STATE = Path("/mnt/HC_Volume_106755700/greenmind-archive-acceptance") / REVISION
READONLY = Path("/etc/greenmind/archive-readonly-20261007")
LIVE = Path("/home/traver/greenmind-archive-production/33fe395")


def main():
    assert os.geteuid() == 0
    target = STATE / "readonly-broker"
    assert not target.exists()
    target.mkdir(mode=0o700)
    account = pwd.getpwnam("greenmind-raw-copy")
    assert account.pw_uid == 996 and account.pw_gid == 986
    values = dict(
        line.split("=", 1)
        for line in (LIVE / "broker.env").read_text().splitlines()
        if "=" in line
    )
    dedicated = dict(
        line.split("=", 1)
        for line in (READONLY / "broker.env").read_text().splitlines()
        if "=" in line
    )
    prefix = "RAW_ARCHIVE_READ_ONLY_SFTP_"
    fields = {prefix + name for name in ("HOST", "USER", "KEY", "KNOWN_HOSTS")}
    assert fields.issubset(dedicated)
    for field in ("KEY", "KNOWN_HOSTS"):
        path = Path(dedicated[prefix + field])
        assert path.parent == READONLY and path.is_file() and not path.is_symlink()
        info = path.stat()
        assert (
            info.st_uid == 0
            and info.st_gid == account.pw_gid
            and info.st_mode & 0o007 == 0
        )
    assert dedicated[prefix + "USER"] == "u676312-sub1"
    assert dedicated[prefix + "HOST"] == "u676312-sub1.your-storagebox.de"
    keys = (
        "RAW_ARCHIVE_STATE_DIR",
        "RAW_ARCHIVE_ENVIRONMENT",
        "RAW_ARCHIVE_READ_SCRATCH_DIR",
        "RAW_ARCHIVE_READ_BROKER_SOCKET",
        "RAW_ARCHIVE_MAX_FILE_BYTES",
    )
    safe = {key: values[key] for key in keys if key in values}
    assert safe["RAW_ARCHIVE_STATE_DIR"] == "/var/lib/greenmind-raw-copy"
    assert safe["RAW_ARCHIVE_ENVIRONMENT"] == "production"
    assert (
        safe["RAW_ARCHIVE_READ_BROKER_SOCKET"]
        == "/run/greenmind-archive-read/read.sock"
    )
    assert Path(safe["RAW_ARCHIVE_READ_SCRATCH_DIR"]).is_dir()
    safe.update({key: dedicated[key] for key in fields})
    safe.update(
        RAW_ARCHIVE_ENABLED="false",
        RAW_ARCHIVE_DELETE_ENABLED="false",
        RAW_ARCHIVE_READS_ACCEPTED="true",
        RAW_ARCHIVE_LEGACY_NULL_ACCEPTED="false",
        RAW_ARCHIVE_DIRECT_S3_BUCKET="greenmind-direct-production-hotspot",
        RETENTION_ENABLED="false",
        DIRECT_RETENTION_ENABLED="false",
        VISUAL_PRUNE_ENABLED="false",
    )
    assert not any(key.startswith("RAW_ARCHIVE_SFTP_") for key in safe)
    env = target / "broker.env"
    with env.open("x") as body:
        body.write(
            "".join(key + "=" + value + "\n" for key, value in sorted(safe.items()))
        )
    os.chown(env, 0, 986)
    env.chmod(0o640)
    # The process's existing access paths and 64 MiB cap are unchanged.
    content = (
        "[Service]\nEnvironmentFile=\nEnvironmentFile="
        + str(env)
        + "\nEnvironment=PYTHONPATH="
        + str(RUNTIME / "backend")
        + "\n"
    )
    override = target / "zz-readonly-" + REVISION + ".conf"
    override.write_text(content)
    override.chmod(0o600)
    manifest = {
        "revision": REVISION,
        "environment_file": str(env),
        "environment_sha256": hashlib.sha256(env.read_bytes()).hexdigest(),
        "override": str(override),
        "override_sha256": hashlib.sha256(override.read_bytes()).hexdigest(),
        "unit": "greenmind-archive-read.service",
        "upload_credentials_present": False,
        "activated": False,
        "deleted_files": 0,
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
