"""Start only the isolated read candidate, after headroom and immutable-code checks."""

import json
import os
import subprocess
import time
from pathlib import Path

from app.raw_archive.observation import protected
from app.raw_archive.policy import checksum
from app.raw_archive.runner import health_probe

RUNTIME = Path(__file__).resolve().parents[3]


def main():
    import httpx

    assert os.geteuid() == 0
    bundle = json.loads((RUNTIME / "bundle.json").read_text())
    for name, digest in bundle["files"].items():
        assert checksum(RUNTIME / name) == digest, "Installed code changed"
    state = (
        Path("/mnt/HC_Volume_106755700/greenmind-delete-preparation")
        / bundle["revision"][:12]
        / "reader"
    )
    manifest = json.loads((state / "manifest.json").read_text())
    assert not manifest["started"]
    probe = health_probe()
    try:
        if not probe() or probe.last.get("available_mib", 0) < 704:
            print(
                json.dumps(
                    {
                        "status": "paused",
                        "guard": probe.last,
                        "required_before_start_mib": 704,
                        "started": False,
                        "deleted_files": 0,
                    }
                )
            )
            return 75
        before = protected()
        command = ["docker", "compose", "-f", str(state / "compose.json")]
        subprocess.run(
            command + ["up", "-d", "api"], check=True, timeout=30, capture_output=True
        )
        ready = False
        try:
            with httpx.Client(timeout=3, trust_env=False) as client:
                for _ in range(15):
                    try:
                        ready = (
                            client.get("http://127.0.0.1:8141/health").status_code
                            == 200
                        )
                    except httpx.HTTPError:
                        pass
                    if ready:
                        break
                    time.sleep(1)
            assert ready and protected() == before, (
                "Candidate or protected services changed"
            )
            assert probe(), "Receiver headroom deteriorated during candidate start"
        except Exception:
            # Stop ONLY the new candidate if acceptance cannot proceed safely.
            subprocess.run(
                command + ["stop", "api"], check=True, timeout=15, capture_output=True
            )
            raise
        manifest["started"] = True
        (state / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "status": "candidate_started",
                    "reader_port": 8141,
                    "routes_changed": False,
                    "receivers_unchanged": True,
                    "deleted_files": 0,
                }
            )
        )
        return 0
    finally:
        probe.close()


if __name__ == "__main__":
    raise SystemExit(main())
