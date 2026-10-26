"""Privately relay existing narrow configured access to the approved archive host."""

import json
import os
import pwd
import tarfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def env(path):
    assert path.is_file() and not path.is_symlink() and not path.stat().st_mode & 0o007
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    )


def main():
    assert os.geteuid() == 0 and os.uname().nodename == "Biolingo"
    root = Path("/home/traver/greenmind-archive-rollout-20261008/offload-access-v4")
    root.mkdir(mode=0o700)
    old = env(Path("/etc/greenmind/raw-copy/storagebox.env"))
    readonly = env(Path("/etc/greenmind/archive-readonly-20261007/broker.env"))
    narrow = env(
        Path(
            "/home/traver/greenmind-archive-acceptance-20261007/greenmind_wav_metadata_b4ae52d674aa/metadata.env"
        )
    )
    target = "/etc/greenmind/archive-offload-v4"
    values = {
        key: value for key, value in old.items() if key.startswith("RAW_ARCHIVE_SFTP_")
    }
    assert values["RAW_ARCHIVE_SFTP_USER"] == "u676312"
    assert values["RAW_ARCHIVE_SFTP_HOST"] == "u676312.your-storagebox.de"
    values.update(
        {
            key: value
            for key, value in readonly.items()
            if key.startswith("RAW_ARCHIVE_READ_ONLY_SFTP_")
        }
    )
    assert values["RAW_ARCHIVE_READ_ONLY_SFTP_USER"] == "u676312-sub1"
    attachments = {
        "upload-key": Path(values["RAW_ARCHIVE_SFTP_KEY"]),
        "upload-known-hosts": Path(values["RAW_ARCHIVE_SFTP_KNOWN_HOSTS"]),
        "readonly-key": Path(values["RAW_ARCHIVE_READ_ONLY_SFTP_KEY"]),
        "readonly-known-hosts": Path(values["RAW_ARCHIVE_READ_ONLY_SFTP_KNOWN_HOSTS"]),
        "probe-token": Path("/etc/greenmind/source-probe/token"),
    }
    for key, filename in [
        ("RAW_ARCHIVE_SFTP_KEY", "upload-key"),
        ("RAW_ARCHIVE_SFTP_KNOWN_HOSTS", "upload-known-hosts"),
        ("RAW_ARCHIVE_READ_ONLY_SFTP_KEY", "readonly-key"),
        ("RAW_ARCHIVE_READ_ONLY_SFTP_KNOWN_HOSTS", "readonly-known-hosts"),
    ]:
        values[key] = target + "/" + filename
    for kind in ("GATEWAY", "DIRECT"):
        key = "RAW_ARCHIVE_" + kind + "_DATABASE_URL"
        uri = urlsplit(narrow[key])
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
        RAW_ARCHIVE_ENVIRONMENT="production",
        RAW_ARCHIVE_WORKER_ROLE="archive",
        RAW_ARCHIVE_SOURCE_PROBE_URL="http://127.0.0.1:18150/source-health",
        RAW_ARCHIVE_SOURCE_PROBE_TOKEN_FILE=target + "/probe-token",
        RAW_ARCHIVE_SOURCE_ID="production-188.245.247.156",
        RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB="640",
        RAW_ARCHIVE_SOURCE_MAX_LOAD="2",
        RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB="800",
        RAW_ARCHIVE_MAX_HOST_LOAD="2",
        RAW_ARCHIVE_SOURCE_MOUNTS="/",
        RAW_ARCHIVE_HEALTH_URLS=",".join(
            f"http://127.0.0.1:{p}/health" for p in (18120, 18003, 18000)
        ),
        RAW_ARCHIVE_DIRECT_S3_BUCKET="greenmind-direct-production-hotspot",
        RAW_ARCHIVE_LEASE_HOST="188.245.247.156",
        RAW_ARCHIVE_LEASE_USER="greenmind-archive-lease",
        RAW_ARCHIVE_LEASE_KEY=target + "/lease-key",
        RAW_ARCHIVE_LEASE_KNOWN_HOSTS=target + "/lease-known-hosts",
        RAW_ARCHIVE_FEATURE_STATE="/var/lib/greenmind/archive/features",
        RAW_ARCHIVE_PYTHON="/opt/greenmind/archive-python-20261008-v4/bin/python3",
        RAW_ARCHIVE_ENABLED="false",
        RAW_ARCHIVE_DELETE_ENABLED="false",
        RAW_ARCHIVE_READS_ACCEPTED="false",
        RAW_ARCHIVE_LEGACY_NULL_ACCEPTED="false",
        RETENTION_ENABLED="false",
        DIRECT_RETENTION_ENABLED="false",
        VISUAL_PRUNE_ENABLED="false",
        PYTHONHOME="/opt/greenmind/archive-python-20261008-v4",
        LD_LIBRARY_PATH="/opt/greenmind/archive-python-20261008-v4/lib",
        PYTHONPATH="/opt/greenmind/archive-preparation/20261008-v4/backend",
        PYTHONDONTWRITEBYTECODE="1",
    )
    with (root / "access.env").open("x") as body:
        os.fchmod(body.fileno(), 0o600)
        body.write("".join(key + "=" + value + "\n" for key, value in values.items()))
    for name, source in attachments.items():
        assert source.is_file() and not source.is_symlink()
        with (root / name).open("xb") as body:
            os.fchmod(body.fileno(), 0o600)
            body.write(source.read_bytes())
    archive = root.parent / "offload-access-v4.tar"
    with archive.open("xb") as body:
        os.fchmod(body.fileno(), 0o600)
        with tarfile.open(fileobj=body, mode="w") as tar:
            for path in sorted(root.iterdir()):
                tar.add(path, arcname=path.name, recursive=False)
        operator = pwd.getpwnam("traver")
        os.fchown(body.fileno(), operator.pw_uid, operator.pw_gid)
    import hashlib

    print(
        json.dumps(
            {
                "target_host": "2.31.22.33",
                "file": str(archive),
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "bytes": archive.stat().st_size,
                "admin_credentials_included": False,
                "deleted_files": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
