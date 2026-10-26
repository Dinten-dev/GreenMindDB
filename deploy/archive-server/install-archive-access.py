"""Install the explicitly approved archive access bundle; no jobs or deletion."""

import hashlib
import json
import os
import pwd
import subprocess
import tarfile
from pathlib import Path

PACKAGE = Path("/home/traver/greenmind-archive-rollout-20261008/offload-access-v4.tar")
PACKAGE_SHA = "7cc9c7354a0ef339cc622d3fef84d6366e5f089dc299431a116b6efc59a768ea"
ROOT = Path("/etc/greenmind/archive-offload-v10")
OLD_ROOT = Path("/etc/greenmind/archive-offload-v4")
NAMES = {
    "access.env", "upload-key", "upload-known-hosts", "readonly-key",
    "readonly-known-hosts", "probe-token",
}


def payload(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size != 20480:
        raise RuntimeError("Exact approved regular package required")
    if hashlib.sha256(path.read_bytes()).hexdigest() != PACKAGE_SHA:
        raise RuntimeError("Approved package hash differs")
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        if len(members) != len(NAMES) or {m.name for m in members} != NAMES:
            raise RuntimeError("Unexpected archive member")
        if any(not m.isfile() or not 0 < m.size <= 8192 for m in members):
            raise RuntimeError("Only bounded regular access files are permitted")
        return {m.name: archive.extractfile(m).read() for m in members}


def main():
    assert os.geteuid() == 0 and os.uname().nodename == "ubuntu-4gb-fsn1-1"
    contents = payload(PACKAGE)
    values = dict(
        line.split("=", 1) for line in contents["access.env"].decode().splitlines()
        if line and not line.startswith("#")
    )
    assert values["RAW_ARCHIVE_SFTP_USER"] == "u676312"
    assert values["RAW_ARCHIVE_READ_ONLY_SFTP_USER"] == "u676312-sub1"
    assert values["RAW_ARCHIVE_WORKER_ROLE"] == "archive"
    for name in (
        "RAW_ARCHIVE_ENABLED", "RAW_ARCHIVE_DELETE_ENABLED",
        "RAW_ARCHIVE_READS_ACCEPTED", "RAW_ARCHIVE_LEGACY_NULL_ACCEPTED",
        "RETENTION_ENABLED", "DIRECT_RETENTION_ENABLED", "VISUAL_PRUNE_ENABLED",
    ):
        assert values[name] == "false"
    assert not ROOT.exists(), "Never replace an existing access installation"
    account = pwd.getpwnam("greenmind-archive-copy")
    assert account.pw_shell == "/usr/sbin/nologin"
    base = ROOT.parent
    assert base.is_dir() and not base.is_symlink() and base.stat().st_uid == 0
    # Traverse only for the isolated copy identity; never expose sibling secrets.
    subprocess.run(
        ["setfacl", "-m", "u:greenmind-archive-copy:--x", str(base)],
        check=True, timeout=5, capture_output=True,
    )
    key = OLD_ROOT / "lease-key"
    known = Path("/etc/greenmind/source-link/known_hosts")
    for file in (key, known):
        assert file.is_file() and not file.is_symlink()
        assert file.stat().st_uid == 0 and not file.stat().st_mode & 0o077
    assert hashlib.sha256(known.read_bytes()).hexdigest() == (
        "ca0bac08edab20db1191d9b78188dbf55c3b795837fbf1d22d8129ace67de2b1"
    )
    values = {k: v.replace(str(OLD_ROOT) + "/", str(ROOT) + "/") for k, v in values.items()}
    values["PYTHONPATH"] = "/opt/greenmind/archive-preparation/20261008-v9/backend"
    # Preserve the established source/local reserves and conservative load limits.
    assert values["RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB"] == "640"
    assert values["RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB"] == "800"
    assert values["RAW_ARCHIVE_SOURCE_MAX_LOAD"] == "2"
    contents["access.env"] = "".join(f"{k}={v}\n" for k, v in values.items()).encode()
    contents["lease-key"] = key.read_bytes()
    contents["lease-known-hosts"] = known.read_bytes()
    ROOT.mkdir(mode=0o750)
    os.chown(ROOT, 0, account.pw_gid)
    for name, body in contents.items():
        with (ROOT / name).open("xb") as file:
            os.fchmod(file.fileno(), 0o640 if name == "access.env" else 0o600)
            os.fchown(file.fileno(), 0 if name == "access.env" else account.pw_uid, account.pw_gid)
            file.write(body)
            file.flush()
            os.fsync(file.fileno())
    state = Path("/var/lib/greenmind/archive/catalog-preparation-20261009")
    assert not state.exists()
    # The private parent was prepared earlier; only the archive copy group traverses.
    parent = state.parent
    assert parent.is_dir() and not parent.is_symlink()
    assert parent.stat().st_uid == 0
    os.chown(parent, 0, account.pw_gid)
    parent.chmod(0o750)
    state.mkdir(mode=0o700)
    os.chown(state, account.pw_uid, account.pw_gid)
    print(json.dumps({
        "status": "installed", "package_sha256": PACKAGE_SHA,
        "private_identity": account.pw_name, "uid": account.pw_uid,
        "config": str(ROOT / "access.env"), "state": str(state),
        "admin_identities_created": [], "jobs_started": False,
        "deleted_files": 0, "public_reader_access_changed": False,
    }))


if __name__ == "__main__":
    main()
