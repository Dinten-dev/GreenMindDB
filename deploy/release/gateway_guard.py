"""Keep cloud maintenance from issuing actions to an unchanged Raspberry Pi.

Prepare a reviewable nginx-only guard, then activate after operator approval.
The guard survives read-service rollback and requires separate removal review.
"""

import argparse
import fcntl
import hashlib
import json
import os
import signal
import subprocess
from pathlib import Path

BEGIN = "    # BEGIN GREENMIND GATEWAY CONTINUITY GUARD\n"
END = "    # END GREENMIND GATEWAY CONTINUITY GUARD\n"
SITES = {"production": ("greenmind-prod", 8000), "staging": ("greenmind-staging", 8001)}


def block(environment):
    port = SITES[environment][1]
    return (
        BEGIN
        + f"""    # Cloud control is paused; measurement/WAV uploads remain on the old API.
    location ~ ^/api/v1/gateway/(desired-state/?$|(app|config)-release/) {{
        default_type application/json;
        return 503 '{{"detail":"Gateway remote control paused for server maintenance"}}';
    }}
    location ~ ^/api/v1/gateways/[^/]+/commands/?$ {{
        default_type application/json;
        return 503 '{{"detail":"Gateway remote control paused for server maintenance"}}';
    }}
    # Installed v1.0.9 handles reset responses in heartbeat AND ingest uploads.
    location ~ ^/api/v1/(gateways/heartbeat|ingest)/?$ {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_connect_timeout 10s;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_intercept_errors on;
        error_page 410 = @gateway_continuity_unavailable;
    }}
    location @gateway_continuity_unavailable {{
        default_type application/json;
        return 503 '{{"detail":"Gateway registration unavailable; keep local configuration"}}';
    }}
"""
        + END
        + "\n"
    )


def require_guard(config, environment):
    if config.count(BEGIN) != 1 or block(environment) not in config:
        raise RuntimeError(
            "Install the reviewed gateway continuity guard before any server release"
        )


def render(config, environment):
    if BEGIN in config or END in config:
        require_guard(config, environment)
        return config
    marker = "    # Authenticated visualization only;"
    if marker not in config:
        marker = "    # API → Backend directly"
    if config.count(marker) != 1:
        raise RuntimeError("Unsupported site configuration; manual review required")
    return config.replace(marker, block(environment) + marker, 1)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace(target, source):
    pending = target.with_suffix(".gateway-guard-pending")
    pending.write_bytes(source.read_bytes())
    pending.chmod(0o644)
    os.replace(pending, target)
    subprocess.run(["nginx", "-t"], check=True, timeout=15)
    subprocess.run(["systemctl", "reload", "nginx"], check=True, timeout=30)


def main():
    os.umask(0o077)

    def interrupted(signum, frame):
        raise InterruptedError("Gateway shield activation interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "activate", "verify"))
    parser.add_argument("--environment", required=True, choices=SITES)
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    target = Path("/etc/nginx/sites-available") / SITES[args.environment][0]
    if args.action == "verify":
        require_guard(target.read_text(), args.environment)
        print("Gateway continuity guard verified")
        return
    if args.directory is None:
        parser.error("--directory is required for prepare/activate")
    root = args.directory.resolve()
    if args.action == "prepare":
        original = target.read_text()
        proposed = render(original, args.environment)
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
        (root / "before.conf").write_text(original)
        (root / "proposed.conf").write_text(proposed)
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "environment": args.environment,
                    "before": digest(root / "before.conf"),
                    "after": digest(root / "proposed.conf"),
                },
                indent=2,
            )
            + "\n"
        )
        print("Prepared gateway guard. Review both configurations before activation.")
        return
    if os.geteuid() != 0:
        raise RuntimeError("The reviewed nginx-only change requires sudo")
    manifest = json.loads((root / "manifest.json").read_text())
    if (
        manifest["environment"] != args.environment
        or digest(target) != manifest["before"]
        or digest(root / "before.conf") != manifest["before"]
        or digest(root / "proposed.conf") != manifest["after"]
        or render((root / "before.conf").read_text(), args.environment)
        != (root / "proposed.conf").read_text()
    ):
        raise RuntimeError("Configuration changed; prepare and review again")
    with Path("/run/lock/greenmind-visualization-proxy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if digest(target) != manifest["before"]:
            raise RuntimeError("Active configuration changed")
        try:
            replace(target, root / "proposed.conf")
        except BaseException:
            replace(target, root / "before.conf")
            raise
    print("Gateway cloud control paused. Raspberry and receiving services unchanged.")


if __name__ == "__main__":
    main()
