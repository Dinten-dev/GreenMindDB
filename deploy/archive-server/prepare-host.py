"""Prepare isolated archive-host budgets and service identities; no deletion/jobs."""

import json
import os
import pwd
import subprocess
from pathlib import Path

ROOT = Path("/var/lib/greenmind")


def main():
    assert os.geteuid() == 0
    assert os.uname().nodename == "ubuntu-4gb-fsn1-1"
    source = Path(__file__).resolve().parent
    users = ["greenmind-archive-copy", "greenmind-archive-read"]
    accounts = []
    for name in users:
        try:
            account = pwd.getpwnam(name)
        except KeyError:
            subprocess.run(
                [
                    "useradd",
                    "--system",
                    "--no-create-home",
                    "--shell",
                    "/usr/sbin/nologin",
                    name,
                ],
                check=True,
            )
            account = pwd.getpwnam(name)
        assert account.pw_shell == "/usr/sbin/nologin"
        accounts.append(
            {"name": name, "uid": account.pw_uid, "gid": account.pw_gid, "admin": False}
        )
    for name in ["archive", "staging", "evidence"]:
        path = ROOT / name
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        assert not path.is_symlink()
    for name in [
        "greenmind.slice",
        "greenmind-archive.slice",
        "greenmind-staging.slice",
    ]:
        incoming = source / name
        target = Path("/etc/systemd/system") / name
        if target.exists():
            assert target.read_bytes() == incoming.read_bytes(), (
                "Existing budget differs"
            )
        else:
            with target.open("xb") as body:
                os.fchmod(body.fileno(), 0o644)
                body.write(incoming.read_bytes())
                body.flush()
                os.fsync(body.fileno())
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(
        [
            "systemctl",
            "start",
            "greenmind.slice",
            "greenmind-archive.slice",
            "greenmind-staging.slice",
        ],
        check=True,
    )
    result = {
        "accounts": accounts,
        "budgets_mib": {"total": 2560, "archive": 1536, "staging": 1024},
        "deletion_enabled": False,
        "archive_jobs_started": False,
        "at": __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .isoformat(),
    }
    path = ROOT / "evidence" / "host-preparation.json"
    if not path.exists():
        with path.open("x") as body:
            os.fchmod(body.fileno(), 0o600)
            json.dump(result, body, indent=2)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
