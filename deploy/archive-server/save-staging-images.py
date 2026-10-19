"""Bounded staging image export; one producer, fixed manifest, no receiver exec."""

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen


def healthy():
    memory = (
        next(
            int(v.split()[1])
            for v in Path("/proc/meminfo").read_text().splitlines()
            if v.startswith("MemAvailable:")
        )
        / 1024
    )
    if memory < 512 or os.getloadavg()[0] > 2:
        return False
    try:
        for port in (8120, 8003, 8000):
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                if response.status != 200:
                    return False
        return True
    except Exception:
        return False


def main():
    assert os.uname().nodename == "Biolingo"
    root = Path(
        "/home/traver/greenmind-archive-rollout-20261008/staging-preparation-v2"
    )
    manifest = json.loads((root / "manifest.json").read_text())
    assert all(v.startswith("sha256:") and len(v) == 71 for v in manifest["images"])
    target = root / "images.tar"
    assert not target.exists(), "Preserve previous export; do not overwrite"
    assert healthy(), "Production resource guard paused export before start"
    digest, size = hashlib.sha256(), 0
    producer = subprocess.Popen(
        ["docker", "save", *manifest["images"]], stdout=subprocess.PIPE
    )
    assert producer.stdout is not None
    deadline, checked = time.monotonic() + 1800, 0
    try:
        with target.open("xb") as output:
            os.fchmod(output.fileno(), 0o600)
            while True:
                if time.monotonic() - checked > 10:
                    while not healthy():
                        assert time.monotonic() < deadline, (
                            "Export guard timeout; partial preserved"
                        )
                        time.sleep(5)
                    checked = time.monotonic()
                block = producer.stdout.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
                digest.update(block)
                size += len(block)
                time.sleep(0.05)  # Bound disk traffic to approximately 20 MiB/s.
            output.flush()
            os.fsync(output.fileno())
        assert producer.wait(timeout=30) == 0, "Incomplete image export"
    finally:
        if producer.poll() is None:
            producer.terminate()
            producer.wait(timeout=10)
    result = {
        "bytes": size,
        "sha256": digest.hexdigest(),
        "images": manifest["images"],
        "deletion_enabled": False,
    }
    with (root / "images-evidence.json").open("x") as output:
        os.fchmod(output.fileno(), 0o600)
        json.dump(result, output, indent=2)
    print(json.dumps({"bytes": size, "sha256": digest.hexdigest()}))


if __name__ == "__main__":
    main()
