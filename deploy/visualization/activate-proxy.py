"""Run with sudo only after reviewing/testing the generated proposal."""

import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parent
assert os.geteuid() == 0, "sudo required"
manifest = json.loads((root / "proxy-manifest.json").read_text())
target = Path(manifest["target"])
assert str(target) in (
    "/etc/nginx/sites-available/greenmind-prod",
    "/etc/nginx/sites-available/greenmind-staging",
)
assert hashlib.sha256(target.read_bytes()).hexdigest() == manifest["before_sha256"], (
    "Installed proxy changed; regenerate/review proposal"
)
proposed = root / "nginx.proposed.conf"
assert hashlib.sha256(proposed.read_bytes()).hexdigest() == manifest["after_sha256"], (
    "Proposal changed"
)
for port, route in (
    (manifest["api_port"], "/health"),
    (manifest["frontend_port"], "/de/app/sensors"),
):
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}{route}", timeout=10
    ) as response:
        assert response.status == 200
validation = json.loads((root / "validation-passed.json").read_text())
assert validation["passed"] and validation["existing_services_unchanged"], (
    "Standby validation not completed"
)
assert time.time() - validation["checked_at"] < 3600, "Repeat stale standby validation"
backup = target.with_name(
    target.name + ".before-visual-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
)
shutil.copy2(target, backup)
try:
    # Atomic replacement; nginx keeps serving the old parsed configuration until reload.
    temporary = target.with_name(target.name + ".visual-pending")
    shutil.copyfile(proposed, temporary)
    os.chmod(temporary, 0o644)
    os.replace(temporary, target)
    subprocess.run(["nginx", "-t"], check=True)
    subprocess.run(["systemctl", "reload", "nginx"], check=True)
except BaseException:
    shutil.copy2(backup, target)
    subprocess.run(["nginx", "-t"], check=True)
    subprocess.run(["systemctl", "reload", "nginx"], check=True)
    raise
(root / "proxy-activated.json").write_text(
    json.dumps(
        {
            "backup": str(backup),
            "activated_at": time.time(),
            "environment": manifest["environment"],
        }
    )
    + "\n"
)
# Pruning is a separate operation after external, authenticated verification.
print(
    "Visualisierungs-Proxy aktiviert. Empfangsdienste unverändert. Historische Daten noch nicht entfernt."
)
