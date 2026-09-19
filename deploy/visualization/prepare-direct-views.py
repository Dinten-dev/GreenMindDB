"""Staging-only preparation; copies connection credentials without printing them."""

import json
import os
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent
assert root == Path("/home/traver/greenmind-visual-staging")
assert (
    json.loads((root / "proxy-activated.json").read_text())["environment"] == "staging"
)
metadata = json.loads(
    subprocess.check_output(["docker", "inspect", "gm-direct-staging-direct-api-1"])
)[0]
values = {}
for line in metadata["Config"]["Env"]:
    key, _, value = line.partition("=")
    if key.startswith("DIRECT_"):
        assert "\n" not in value and "\r" not in value
        values[key] = value
assert values["DIRECT_ENVIRONMENT"] == "staging"
assert "greenmind_direct_staging" in values["DIRECT_DATABASE_URL"]
assert values["DIRECT_S3_BUCKET"].startswith("greenmind-direct-staging")
values.update(
    VISUAL_DIRECT_ENABLED="true",
    VISUAL_DIRECT_FINE_DAYS="1",
    DIRECT_INGEST_ENABLED="false",
)
os.umask(0o077)
(root / "runtime-direct.env").write_text(
    (root / "runtime.env").read_text()
    + "\n"
    + "".join(f"{key}={value}\n" for key, value in values.items())
)
containers = [
    json.loads(line)
    for line in subprocess.check_output(["docker", "ps", "--format", "{{json .Names}}"])
    .decode()
    .splitlines()
]
# Persist start times for all receivers and existing workers, excluding only the
# two explicitly replaced visualization services.
names = [
    name
    for name in containers
    if name
    not in (
        "gm-visual-staging-visual-api-1",
        "gm-visual-staging-visual-frontend-1",
        "gm-visual-staging-visual-direct-worker-1",
    )
]
state = json.loads(subprocess.check_output(["docker", "inspect", *names]))
(root / "direct-before-containers.json").write_text(
    json.dumps(
        {item["Name"].lstrip("/"): item["State"]["StartedAt"] for item in state},
        indent=2,
    )
)
print(
    "Staging connection prepared; credentials not printed; existing services recorded."
)
