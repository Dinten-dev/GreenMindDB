"""Install bounded telemetry and a dedicated, forwarding-only archive identity."""

import hashlib
import json
import os
import pwd
import secrets
import subprocess
from pathlib import Path
from urllib.request import urlopen

ACCOUNT = "greenmind-archive-link"
RECEIVERS = (
    "gm-zones-production-2e687bec010a-application-1",
    "gm-direct-production-direct-api-1",
    "greenminddb-backend-1",
)
OPEN = (
    "127.0.0.1:5432",
    "127.0.0.1:9000",
    "127.0.0.1:8150",
    "127.0.0.1:8120",
    "127.0.0.1:8003",
    "127.0.0.1:8000",
    "172.28.21.1:9443",
    "172.28.20.1:9444",
)
LISTEN = (28124, 28125, 28126, 28002, 23124, 23009, 29002, 28141)


def receivers():
    return subprocess.check_output(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Name}} {{.State.StartedAt}} {{.RestartCount}} {{.State.Running}}",
            *RECEIVERS,
        ],
        text=True,
    )


def install(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    assert not path.is_symlink()
    if path.exists():
        assert path.read_bytes() == data, "Existing source provisioning differs"
    else:
        with path.open("xb") as body:
            os.fchmod(body.fileno(), mode)
            body.write(data)
            body.flush()
            os.fsync(body.fileno())


def main():
    assert os.geteuid() == 0 and os.uname().nodename == "Biolingo"
    before = receivers()
    for port in (8120, 8003, 8000):
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            assert response.status == 200
    source = Path(__file__).resolve().parent
    key = (source / "source-link-key.pub").read_text().strip()
    assert key.startswith("ssh-ed25519 ") and "\n" not in key
    home = Path("/var/lib/greenmind-archive-link")
    try:
        account = pwd.getpwnam(ACCOUNT)
    except KeyError:
        subprocess.run(
            [
                "useradd",
                "--system",
                "--home-dir",
                str(home),
                "--no-create-home",
                "--shell",
                "/usr/sbin/nologin",
                ACCOUNT,
            ],
            check=True,
        )
        account = pwd.getpwnam(ACCOUNT)
    assert account.pw_shell == "/usr/sbin/nologin" and account.pw_dir == str(home)
    options = [
        "restrict",
        "port-forwarding",
        'from="2.31.22.33"',
        'command="/bin/false"',
    ]
    options += [f'permitopen="{v}"' for v in OPEN]
    options += [f'permitlisten="127.0.0.1:{v}"' for v in LISTEN]
    install(
        home / ".ssh/authorized_keys",
        (",".join(options) + " " + key + "\n").encode(),
        0o644,
    )
    # Root ownership prevents this tunnel identity from broadening its own authorization.
    for directory in (home, home / ".ssh"):
        directory.chmod(0o755)
    install(
        Path("/opt/greenmind/source-probe/source_probe.py"),
        (source / "source_probe.py").read_bytes(),
        0o644,
    )
    Path("/opt/greenmind/source-probe").chmod(0o755)
    token = Path("/etc/greenmind/source-probe/token")
    if not token.exists():
        install(token, secrets.token_hex(32).encode(), 0o600)
    assert not token.is_symlink() and token.stat().st_mode & 0o077 == 0
    install(
        Path("/etc/systemd/system/greenmind-source-probe.service"),
        (source / "source-probe.service").read_bytes(),
        0o644,
    )
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "enable", "--now", "greenmind-source-probe.service"], check=True
    )
    assert receivers() == before, "Production receiver changed"
    evidence = {
        "account": ACCOUNT,
        "uid": account.pw_uid,
        "admin": False,
        "source_ip": "2.31.22.33",
        "permitted_open": OPEN,
        "permitted_listen": LISTEN,
        "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
        "receiver_before_after": before,
        "deletion_enabled": False,
    }
    install(
        source / "source-preparation.json",
        json.dumps(evidence, indent=2).encode(),
        0o600,
    )
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()
