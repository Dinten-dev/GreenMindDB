"""Prepare isolated service directories, copying credentials without displaying them."""

import json
import os
import subprocess
import sys
from pathlib import Path

name = sys.argv[1]
assert name in ("staging", "production")
root = Path(__file__).resolve().parent
prefix, network, gateway, api, front = (
    ("greenminddb", "greenminddb_default", "172.28.20.1", 8004, 3004)
    if name == "production"
    else ("gm-staging", "gm-staging_default", "172.28.21.1", 8005, 3005)
)
metadata = json.loads(
    subprocess.check_output(["docker", "inspect", prefix + "-backend-1"])
)[0]
keep = (
    "DATABASE_URL",
    "JWT_SECRET_KEY",
    "JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
    "S3_ENDPOINT",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "S3_REGION",
    "COOKIE_SECURE",
    "COOKIE_DOMAIN",
    "SENSOR_EXPORT_MAX_ROWS",
    "SENSOR_EXPORT_MAX_BYTES",
    "SENSOR_EXPORT_MAX_KINDS",
)
values = {}
for value in metadata["Config"]["Env"]:
    key, _, item = value.partition("=")
    if key in keep:
        assert "\n" not in item and "\r" not in item
        values[key] = item
assert all(
    values.get(k)
    for k in (
        "DATABASE_URL",
        "JWT_SECRET_KEY",
        "S3_ENDPOINT",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
    )
)
# development avoids unrelated release-signing validations; secure auth values above persist.
values["ENVIRONMENT"] = "development"
os.umask(0o077)
# Compose raw format avoids interpolation of literal $ in inherited credentials.
(root / "runtime.env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))
(root / ".env").write_text(
    f"VISUAL_UID={os.getuid()}\nVISUAL_GID={os.getgid()}\nVISUAL_FRONTEND_IMAGE=greenmind-visual-frontend:20260915-{'production' if name == 'production' else '1'}\nVISUAL_PROJECT=gm-visual-{name}\nVISUAL_NETWORK={network}\nVISUAL_BRIDGE_GATEWAY={gateway}\nVISUAL_API_PORT={api}\nVISUAL_FRONTEND_PORT={front}\n"
)
for directory in ("archive", "state"):
    (root / directory).mkdir(mode=0o700, exist_ok=True)
services = [
    prefix + "-" + service + "-1"
    for service in (
        "backend",
        "postgres",
        "minio",
        "frontend",
        "feature-worker",
        "retention-worker",
    )
]
if name == "staging":
    services.extend(
        ("gm-direct-staging-direct-api-1", "gm-direct-staging-direct-worker-1")
    )
(root / "before-containers.json").write_text(
    json.dumps(
        {
            service: json.loads(
                subprocess.check_output(["docker", "inspect", service])
            )[0]["State"]["StartedAt"]
            for service in services
        },
        indent=2,
    )
    + "\n"
)

assert os.stat(root / "archive").st_dev != os.stat("/mnt/HC_Volume_106755700").st_dev, (
    "Archive must use separate root filesystem"
)
print(name + ": isolated configuration prepared; credentials not displayed")
