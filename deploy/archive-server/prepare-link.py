"""Install the approved private transports; no production shell or public listener."""

import json
import os
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen
import time


def main():
    assert os.geteuid() == 0 and os.uname().nodename == "ubuntu-4gb-fsn1-1"
    incoming = Path("/home/traver/greenmind-archive-rollout-20261008")
    config = Path("/etc/greenmind/source-link")
    config.mkdir(parents=True, exist_ok=True, mode=0o700)
    for origin, name in [
        ("source-link-key", "key"),
        ("main-known-hosts", "known_hosts"),
        ("source-probe-token", "probe-token"),
    ]:
        target = config / name
        assert not target.is_symlink()
        if target.exists():
            assert target.read_bytes() == (incoming / origin).read_bytes()
        else:
            with target.open("xb") as body:
                os.fchmod(body.fileno(), 0o600)
                body.write((incoming / origin).read_bytes())
    networks = [
        ("gm-archivehost-staging", "172.28.21.0/24", "172.28.21.1"),
        ("gm-archive-source", "172.28.20.0/24", "172.28.20.1"),
    ]
    for name, subnet, gateway in networks:
        result = subprocess.run(
            ["docker", "network", "inspect", name], capture_output=True
        )
        if result.returncode:
            command = [
                "docker",
                "network",
                "create",
                "--subnet",
                subnet,
                "--gateway",
                gateway,
            ]
            if name == "gm-archivehost-staging":
                command += [
                    "--label",
                    "com.docker.compose.project=gm-archivehost-staging",
                    "--label",
                    "com.docker.compose.network=staging",
                ]
            subprocess.run(command + [name], check=True, stdout=subprocess.DEVNULL)
        else:
            network = json.loads(result.stdout)[0]
            assert network["IPAM"]["Config"] == [{"Subnet": subnet, "Gateway": gateway}]
    target = Path("/etc/systemd/system/greenmind-source-link.service")
    body = (incoming / "source-link.service").read_bytes()
    if target.exists():
        assert target.read_bytes() == body
    else:
        with target.open("xb") as output:
            os.fchmod(output.fileno(), 0o644)
            output.write(body)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "enable", "--now", "greenmind-source-link.service"], check=True
    )
    request = Request(
        "http://127.0.0.1:18150/source-health",
        headers={
            "Authorization": "Bearer " + (config / "probe-token").read_text().strip()
        },
    )
    result = None
    for _ in range(10):
        try:
            with urlopen(request, timeout=8) as response:
                result = json.load(response)
                break
        except Exception:
            time.sleep(2)
    assert result and result["source"] == "production-188.245.247.156"
    assert result["routes"] == {"8120": 200, "8003": 200, "8000": 200}
    print(
        json.dumps(
            {
                "transport": "ready",
                "source": result["source"],
                "source_available_mib": result["available_mib"],
                "source_routes": result["routes"],
                "public_listeners": False,
                "deletion_enabled": False,
            }
        )
    )


if __name__ == "__main__":
    main()
