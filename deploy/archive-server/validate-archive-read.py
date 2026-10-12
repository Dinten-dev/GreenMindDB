"""Two bounded independent WAV readbacks through the dedicated read identity."""

import argparse
import hashlib
import json
import os
import pwd
import subprocess
import wave
from datetime import UTC, datetime
from pathlib import Path

RUNTIME = Path("/opt/greenmind/archive-preparation/20261008-v9")
ACCESS = Path("/etc/greenmind/archive-reader-validation-v10")
STATE = Path("/var/lib/greenmind/archive-reader-validation-20261009")
PYTHON = "/opt/greenmind/archive-python-20261008-v4/bin/python3"
FIELDS = {"RAW_ARCHIVE_READ_ONLY_SFTP_" + name for name in ("HOST", "USER", "KEY", "KNOWN_HOSTS")}


def private_write(path, body, uid, gid, mode=0o600):
    with path.open("xb") as file:
        os.fchown(file.fileno(), uid, gid)
        os.fchmod(file.fileno(), mode)
        file.write(body)
        file.flush()
        os.fsync(file.fileno())


def worker(folder):
    from app.raw_archive.policy import Recording, checksum
    from app.raw_archive.read_only_box import configured_read_only_box

    assert os.geteuid() == pwd.getpwnam("greenmind-archive-read").pw_uid
    assert folder.parent == STATE and not folder.is_symlink()
    assert not any(key.startswith("RAW_ARCHIVE_SFTP_") for key in os.environ)
    requests = json.loads((folder / "requests.json").read_text())
    assert set(requests) == {"gateway", "direct"}
    box = configured_read_only_box()
    assert box is not None
    try:
        results = []
        for kind, receipt in requests.items():
            record = Recording.from_receipt(receipt["recording"])
            assert record.kind == kind and 0 < record.size <= 1024**2
            assert receipt["destination"] == box.identity
            assert receipt["remote_key"] == record.remote_key("production")
            target = folder / (kind + "-archive.wav")
            box.download(receipt["remote_key"], target, record.size)
            assert target.stat().st_size == record.size and checksum(target) == record.sha256
            with wave.open(str(target), "rb") as audio:
                frames = audio.getnframes()
                assert frames > 0
                assert len(audio.readframes(frames)) == frames * audio.getnchannels() * audio.getsampwidth()
            results.append({"kind": kind, "bytes": record.size, "sha256": record.sha256, "full_decode": True})
        proof = {
            "status": "passed", "independent_read_identity": "u676312-sub1",
            "readbacks": results, "complete_catalog": False,
            "reader_authorization_accepted": False, "provider_snapshot_accepted": False,
            "deletion_readiness": False, "deleted_files": 0,
        }
        private_write(folder / "verified.json", json.dumps(proof, indent=2).encode(), os.getuid(), os.getgid())
        print(json.dumps(proof))
    finally:
        box.close()


def install_access():
    assert os.geteuid() == 0
    account = pwd.getpwnam("greenmind-archive-read")
    assert account.pw_shell == "/usr/sbin/nologin"
    source = Path("/etc/greenmind/archive-offload-v10/access.env")
    source_values = dict(line.split("=", 1) for line in source.read_text().splitlines() if line)
    assert FIELDS.issubset(source_values)
    for name, digest in json.loads((RUNTIME / "files.json").read_text()).items():
        assert hashlib.sha256((RUNTIME / name).read_bytes()).hexdigest() == digest
    if not ACCESS.exists():
        ACCESS.mkdir(mode=0o750)
        os.chown(ACCESS, 0, account.pw_gid)
        subprocess.run(["setfacl", "-m", "u:greenmind-archive-read:--x", str(ACCESS.parent)], check=True, timeout=5)
        values = {key: source_values[key] for key in FIELDS}
        for name in ("KEY", "KNOWN_HOSTS"):
            key = "RAW_ARCHIVE_READ_ONLY_SFTP_" + name
            incoming = Path(values[key])
            assert incoming.is_file() and not incoming.is_symlink()
            target = ACCESS / name.lower()
            private_write(target, incoming.read_bytes(), account.pw_uid, account.pw_gid)
            values[key] = str(target)
        values.update(
            PYTHONPATH=str(RUNTIME / "backend"),
            PYTHONHOME="/opt/greenmind/archive-python-20261008-v4",
            LD_LIBRARY_PATH="/opt/greenmind/archive-python-20261008-v4/lib",
            PYTHONDONTWRITEBYTECODE="1",
        )
        private_write(ACCESS / "read.env", "".join(f"{k}={v}\n" for k, v in values.items()).encode(), 0, account.pw_gid, 0o640)
    assert ACCESS.is_dir() and not ACCESS.is_symlink() and ACCESS.stat().st_uid == 0
    assert not ACCESS.stat().st_mode & 0o007
    assert (ACCESS / "read.env").stat().st_uid == 0
    existing = dict(line.split("=", 1) for line in (ACCESS / "read.env").read_text().splitlines() if line)
    assert set(existing) == FIELDS | {"PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH", "PYTHONDONTWRITEBYTECODE"}
    assert existing["RAW_ARCHIVE_READ_ONLY_SFTP_USER"] == "u676312-sub1"
    assert existing["RAW_ARCHIVE_READ_ONLY_SFTP_HOST"] == "u676312-sub1.your-storagebox.de"
    for name in ("KEY", "KNOWN_HOSTS"):
        key = "RAW_ARCHIVE_READ_ONLY_SFTP_" + name
        assert Path(existing[key]).parent == ACCESS
        assert Path(existing[key]).stat().st_uid == account.pw_uid
        assert not Path(existing[key]).stat().st_mode & 0o077
        assert hashlib.sha256(Path(existing[key]).read_bytes()).digest() == hashlib.sha256(Path(source_values[key]).read_bytes()).digest()
    assert not STATE.is_symlink()
    if STATE.exists():
        assert STATE.stat().st_uid == account.pw_uid and not STATE.stat().st_mode & 0o077
    STATE.mkdir(mode=0o700, exist_ok=True)
    os.chown(STATE, account.pw_uid, account.pw_gid)
    return account


def dispatch(requests):
    assert os.geteuid() == 0 and requests.is_file() and requests.stat().st_size <= 16384
    account = install_access()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    folder = STATE / stamp
    folder.mkdir(mode=0o700)
    os.chown(folder, account.pw_uid, account.pw_gid)
    private_write(folder / "requests.json", requests.read_bytes(), account.pw_uid, account.pw_gid)
    props = {
        "User": account.pw_name, "Group": account.pw_name,
        "Slice": "greenmind-archive.slice", "EnvironmentFile": str(ACCESS / "read.env"),
        "MemoryMax": "128M", "MemorySwapMax": "0", "CPUQuota": "10%",
        "Nice": "19", "IOSchedulingClass": "idle", "TasksMax": "16",
        "Type": "oneshot", "TimeoutStartSec": "120", "NoNewPrivileges": "true",
        "PrivateTmp": "true", "ProtectSystem": "strict", "ProtectHome": "true",
        "ReadWritePaths": str(folder), "UMask": "0077",
    }
    unit = "greenmind-archive-read-validation-" + stamp.lower()
    command = ["systemd-run", "--no-block", "--unit=" + unit]
    command += ["--property=" + k + "=" + v for k, v in props.items()]
    command += [PYTHON, str(Path(__file__).resolve()), "--worker", str(folder)]
    subprocess.run(command, check=True, capture_output=True, timeout=15)
    print(json.dumps({"unit": unit, "state": str(folder), "upload_credentials_present": False, "deleted_files": 0}))


def main():
    assert os.uname().nodename == "ubuntu-4gb-fsn1-1"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--requests", type=Path)
    parser.add_argument("--install-only", action="store_true")
    args = parser.parse_args()
    if args.install_only:
        assert args.worker is None and args.requests is None
        account = install_access()
        print(json.dumps({"read_identity": account.pw_name, "upload_credentials_present": False,
                          "jobs_started": False, "deleted_files": 0}))
    elif args.worker:
        worker(args.worker)
    else:
        assert args.requests is not None
        dispatch(args.requests)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "blocked", "error_type": type(error).__name__, "deleted_files": 0}))
        raise SystemExit(1) from None
