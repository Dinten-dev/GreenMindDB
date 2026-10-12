"""Prepare and syntax-test an exact current GET-only reader overlay; never publish."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

EXPECTED = "f4e7b516852a52152e03685f59ec1b396c5634af269114e5c81e9c5f28e95436"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def private_new(path, body):
    with path.open("xb") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(body)
        output.flush()
        os.fsync(output.fileno())


def main():
    assert os.geteuid() == 0 and os.uname().nodename == "Biolingo"
    sys.path.insert(0, "/opt/greenmind/archive-preparation/20261008-v9/backend")
    from app.raw_archive.compat_proxy import draft, rollback
    from app.raw_archive.observation import protected

    target = Path("/etc/nginx/sites-available/greenmind-prod")
    assert sha(target) == EXPECTED, "Inspect any newer Production edit first"
    before = protected()
    original = target.read_text()
    candidate = draft(original, EXPECTED, reader_port=8141)
    assert rollback(candidate, reader_port=8141) == original
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    root = Path("/home/traver/greenmind-archive-rollout-20261008") / (
        "reader-proxy-" + stamp
    )
    root.mkdir(mode=0o700)
    private_new(root / "before.conf", original.encode())
    private_new(root / "candidate.conf", candidate.encode())
    sites = root / "syntax-sites"
    sites.mkdir(mode=0o700)
    matches = 0
    for item in Path("/etc/nginx/sites-enabled").iterdir():
        assert item.is_file()
        if item.resolve() == target.resolve():
            private_new(sites / item.name, candidate.encode())
            matches += 1
        else:
            (sites / item.name).symlink_to(item.resolve())
    assert matches == 1, "Exactly one live Production include is required"
    global_config = Path("/etc/nginx/nginx.conf").read_text()
    line = "include /etc/nginx/sites-enabled/*;"
    assert global_config.count(line) == 1
    replacement = global_config.replace(line, f"include {sites}/*;")
    private_new(root / "nginx.conf", replacement.encode())
    checked = subprocess.run(
        ["nginx", "-t", "-c", str(root / "nginx.conf")],
        capture_output=True,
        timeout=10,
    )
    assert checked.returncode == 0, "Candidate syntax rejected; private files retained"
    assert target.read_text() == original and protected() == before
    proof = {
        "at": datetime.now(UTC).isoformat(),
        "before_sha256": EXPECTED,
        "candidate_sha256": sha(root / "candidate.conf"),
        "prepared_directory": str(root),
        "syntax_passed": True,
        "rollback_byte_exact": True,
        "receivers_unchanged": True,
        "published": False,
        "reader_acceptance": False,
        "fresh_release_binding_required": True,
        "deleted_files": 0,
    }
    private_new(root / "proof.json", json.dumps(proof, sort_keys=True).encode())
    print(
        json.dumps(
            {
                key: proof[key]
                for key in (
                    "syntax_passed",
                    "rollback_byte_exact",
                    "receivers_unchanged",
                    "published",
                    "deleted_files",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
