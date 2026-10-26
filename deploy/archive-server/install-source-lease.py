"""Provision only a forced catalog-lock identity; no admin or upload access."""

import argparse
import json
import os
import pwd
import re
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-key", type=Path, required=True)
    args = parser.parse_args()
    assert os.geteuid() == 0 and os.uname().nodename == "Biolingo"
    root = Path(__file__).resolve().parents[2]
    key = args.public_key.read_text().strip()
    assert re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?", key)
    lock = Path("/var/lib/greenmind-archive-coordination")
    files = [
        lock / name
        for name in ("jobs.lock", "catalog.request", "copy.request", "export.request")
    ]
    assert all(
        p.is_file() and not p.is_symlink() and p.stat().st_uid == 0 for p in files
    )
    name = "greenmind-archive-lease"
    home = Path("/var/lib/greenmind-source-lease")
    try:
        account = pwd.getpwnam(name)
    except KeyError:
        subprocess.run(
            [
                "useradd",
                "--system",
                "--home-dir",
                str(home),
                "--shell",
                "/bin/sh",
                name,
            ],
            check=True,
        )
        account = pwd.getpwnam(name)
    assert account.pw_dir == str(home) and account.pw_shell == "/bin/sh"
    home.mkdir(mode=0o755)
    ssh = home / ".ssh"
    ssh.mkdir(mode=0o700)
    os.chown(ssh, account.pw_uid, account.pw_gid)
    command = f"/usr/bin/env PYTHONPATH={root}/backend /opt/greenmind/raw-copy/20260925/venv/bin/python -m app.raw_archive.remote_lease --serve"
    authorized = f'from="2.31.22.33",restrict,command="{command}" {key}\n'
    with (ssh / "authorized_keys").open("x") as body:
        os.fchmod(body.fileno(), 0o600)
        os.fchown(body.fileno(), account.pw_uid, account.pw_gid)
        body.write(authorized)
    subprocess.run(["setfacl", "-m", f"u:{name}:rx", str(lock)], check=True)
    subprocess.run(["setfacl", "-m", f"u:{name}:rw", *map(str, files)], check=True)
    report = {
        "account": name,
        "uid": account.pw_uid,
        "admin": False,
        "sudo": False,
        "source_ip": "2.31.22.33",
        "forced_operation": "catalog",
        "maximum_seconds": 120,
        "source_minimum_memory_mib": 640,
        "receiver_changes": False,
        "deleted_files": 0,
    }
    with (home / "installation.json").open("x") as body:
        os.fchmod(body.fileno(), 0o600)
        json.dump(report, body)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
