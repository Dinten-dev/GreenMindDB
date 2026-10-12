"""Change only the forced-command package pointer; retain the old authorization."""

import hashlib
import json
import os
import pwd
import tempfile
from pathlib import Path


def main():
    assert os.geteuid() == 0 and os.uname().nodename == "Biolingo"
    before = Path("/opt/greenmind/archive-preparation/20261008-v4")
    after = Path(__file__).resolve().parents[2]
    assert after == Path("/opt/greenmind/archive-preparation/20261008-v8")
    hashes = json.loads((after / "files.json").read_text())
    assert all(
        hashlib.sha256((after / name).read_bytes()).hexdigest() == value
        for name, value in hashes.items()
    )
    account = pwd.getpwnam("greenmind-archive-lease")
    target = Path(account.pw_dir) / ".ssh/authorized_keys"
    raw = target.read_bytes()
    old = f"PYTHONPATH={before}/backend".encode()
    new = f"PYTHONPATH={after}/backend".encode()
    assert (
        raw.count(old) == 1
        and raw.count(b"command=") == 1
        and b'from="2.31.22.33",restrict,' in raw
    )
    assert b" -m app.raw_archive.remote_lease --serve" in raw
    retained = target.parent.parent / "authorization-v4.backup"
    with retained.open("xb") as body:
        os.fchmod(body.fileno(), 0o600)
        body.write(raw)
    fd, name = tempfile.mkstemp(prefix=".forced-lease-", dir=target.parent)
    with os.fdopen(fd, "wb") as body:
        os.fchmod(body.fileno(), 0o600)
        os.fchown(body.fileno(), account.pw_uid, account.pw_gid)
        body.write(raw.replace(old, new))
        body.flush()
        os.fsync(body.fileno())
    assert target.read_bytes() == raw, "Concurrent forced-command change"
    os.replace(name, target)
    print(
        json.dumps(
            {
                "forced_runtime": str(after),
                "previous_authorization_sha256": hashlib.sha256(raw).hexdigest(),
                "new_admins": 0,
                "receiver_changes": False,
                "deleted_files": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
