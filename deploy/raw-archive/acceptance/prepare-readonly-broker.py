"""Prepare replacement private broker credentials; activation remains a guarded step."""

import hashlib
import json
import os
import pwd
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3]
READONLY = Path("/etc/greenmind/archive-readonly-20261007")
LIVE = Path("/home/traver/greenmind-archive-production/33fe395")


def main():
    assert os.geteuid() == 0
    revision = json.loads((PACKAGE / "bundle.json").read_text())["revision"][:12]
    assert len(revision) == 12 and all(c in "0123456789abcdef" for c in revision)
    runtime = Path("/opt/greenmind/archive-acceptance") / revision
    state = Path("/mnt/HC_Volume_106755700/greenmind-archive-acceptance") / revision
    target = state / "readonly-broker"
    target.mkdir(mode=0o700, exist_ok=True)
    assert (
        not target.is_symlink()
        and target.stat().st_uid == 0
        and target.stat().st_mode & 0o077 == 0
    )
    assert set(path.name for path in target.iterdir()) <= {
        "broker.env",
        "manifest.json",
        "zz-readonly-" + revision + ".conf",
    }
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

    def checked_write(path, content, mode):
        if path.exists():
            assert (
                not path.is_symlink()
                and path.stat().st_uid == 0
                and path.read_text() == content
            )
            assert path.stat().st_mode & 0o777 == mode
        else:
            with path.open("x") as body:
                os.fchmod(body.fileno(), mode)
                body.write(content)
                body.flush()
                os.fsync(body.fileno())

    env = target / "broker.env"
    checked_write(
        env,
        "".join(key + "=" + value + "\n" for key, value in sorted(safe.items())),
        0o640,
    )
    os.chown(env, 0, 986)
    env.chmod(0o640)
    # The process's existing access paths and 64 MiB cap are unchanged.
    content = (
        "[Service]\nEnvironmentFile=\nEnvironmentFile="
        + str(env)
        + "\nEnvironment=PYTHONPATH="
        + str(runtime / "backend")
        + "\n"
    )
    override = target / ("zz-readonly-" + revision + ".conf")
    checked_write(override, content, 0o600)
    manifest = {
        "revision": revision,
        "environment_file": str(env),
        "environment_sha256": hashlib.sha256(env.read_bytes()).hexdigest(),
        "override": str(override),
        "override_sha256": hashlib.sha256(override.read_bytes()).hexdigest(),
        "unit": "greenmind-archive-read.service",
        "upload_credentials_present": False,
        "activated": False,
        "deleted_files": 0,
    }
    checked_write(
        target / "manifest.json", json.dumps(manifest, indent=2) + "\n", 0o600
    )
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
