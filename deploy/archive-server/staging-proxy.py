"""Minimal staging-only ports switch; exact hashes, nginx validation and rollback."""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path("/home/traver/greenmind-archive-rollout-20261008/staging-preparation-v2")
PROXY = Path("/etc/nginx/sites-available/greenmind-staging")
ORIGINAL = "6b0737287aa88ffaa15a26cf5351f78088a9df3198837fdc94c9dc2ae08e3b2c"
PORTS = {8124: 28124, 8125: 28125, 8126: 28126, 8002: 28002, 3124: 23124, 3009: 23009}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def variants(original):
    assert sha(original) == ORIGINAL
    assert original.count(b"127.0.0.1:9002") == 1
    tls = original.replace(b"127.0.0.1:9002", b"127.0.0.1:29002")
    complete = tls
    for old, new in PORTS.items():
        before, after = f"127.0.0.1:{old}".encode(), f"127.0.0.1:{new}".encode()
        assert before in complete
        complete = complete.replace(before, after)
    return {"original": original, "tls": tls, "complete": complete}


def publish(body):
    fd, temporary = tempfile.mkstemp(prefix=".greenmind-staging-", dir=PROXY.parent)
    with os.fdopen(fd, "wb") as output:
        os.fchmod(output.fileno(), 0o644)
        output.write(body)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, PROXY)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("tls", "complete", "rollback"), required=True
    )
    args = parser.parse_args()
    assert os.geteuid() == 0 and os.uname().nodename == "Biolingo"
    backup = ROOT / "proxy-original.conf"
    previous = PROXY.read_bytes()
    if not backup.exists():
        assert sha(previous) == ORIGINAL
        with backup.open("xb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(previous)
    bodies = variants(backup.read_bytes())
    assert sha(previous) in {sha(v) for v in bodies.values()}, (
        "Concurrent staging proxy edit"
    )
    target = "original" if args.mode == "rollback" else args.mode
    template = "{{.Name}} {{.State.StartedAt}} {{.RestartCount}} {{.State.Running}}"
    names = [
        "gm-zones-production-2e687bec010a-application-1",
        "gm-direct-production-direct-api-1",
        "greenminddb-backend-1",
    ]
    before = subprocess.check_output(
        ["docker", "inspect", "--format", template, *names]
    )
    if target != "original":
        ports = [29002] if target == "tls" else [29002, *PORTS.values()]
        for port in ports:
            path = (
                "/minio/health/live"
                if port == 29002
                else "/de"
                if port in (23124, 23009)
                else "/health"
            )
            with urlopen(f"http://127.0.0.1:{port}{path}", timeout=8) as response:
                assert response.status == 200
    try:
        publish(bodies[target])
        subprocess.run(["nginx", "-t"], check=True, capture_output=True)
        subprocess.run(["systemctl", "reload", "nginx"], check=True)
        for port in (8120, 8003, 8000):
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                assert response.status == 200
        assert (
            subprocess.check_output(["docker", "inspect", "--format", template, *names])
            == before
        )
    except BaseException:
        assert PROXY.read_bytes() == bodies[target], (
            "Concurrent proxy change prevents rollback"
        )
        publish(previous)
        subprocess.run(["nginx", "-t"], check=True, capture_output=True)
        subprocess.run(["systemctl", "reload", "nginx"], check=True)
        raise
    print(
        json.dumps(
            {
                "mode": target,
                "proxy_sha256": sha(bodies[target]),
                "production_receivers_unchanged": True,
                "deletion_enabled": False,
            }
        )
    )


if __name__ == "__main__":
    main()
