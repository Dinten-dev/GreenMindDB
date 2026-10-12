"""Align only the private legacy Staging API with its existing 0023 schema."""

import hashlib
import json
import os
import subprocess
from pathlib import Path


def main():
    assert os.geteuid() == 0 and os.uname().nodename == "ubuntu-4gb-fsn1-1"
    root = Path("/var/lib/greenmind/staging")
    old = root / "compose.json"
    proof = json.loads((root / "images-verified.json").read_text())
    assert hashlib.sha256(old.read_bytes()).hexdigest() == proof["compose_sha256"]
    config = json.loads(old.read_text())
    backend, accepted = (
        config["services"]["base-backend"],
        config["services"]["admin-application"],
    )
    head = subprocess.check_output(
        ["docker", "exec", accepted["container_name"], "alembic", "heads"],
        text=True,
        timeout=15,
    ).strip()
    assert head == "0023 (head)", "Candidate image schema differs"
    version = subprocess.check_output(
        [
            "docker",
            "exec",
            "gm-staging-postgres-1",
            "psql",
            "-U",
            "plantuser",
            "-d",
            "plantdb_staging",
            "-Xt",
            "--set",
            "ON_ERROR_STOP=on",
            "-c",
            "BEGIN READ ONLY; SET LOCAL statement_timeout=3000; SELECT version_num FROM alembic_version; COMMIT;",
        ],
        text=True,
        timeout=8,
    )
    assert [
        line.strip() for line in version.splitlines() if line.strip().isdigit()
    ] == ["0023"]
    previous = backend["image"]
    backend["image"] = accepted["image"]
    backend["environment"]["GUNICORN_WORKERS"] = "1"
    assert backend["container_name"] == "gm-staging-backend-1"
    assert backend["cgroup_parent"] == "greenmind-staging.slice"
    target = root / "compose-v6.json"
    with target.open("x") as body:
        os.fchmod(body.fileno(), 0o600)
        json.dump(config, body, indent=2)
    evidence = {
        "previous_image": previous,
        "candidate_image": backend["image"],
        "schema_before": "0023",
        "image_head": "0023",
        "workers": 1,
        "original_compose_sha256": proof["compose_sha256"],
        "candidate_compose_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "production_changed": False,
        "deleted_files": 0,
    }
    with (root / "runtime-correction-v6.json").open("x") as body:
        os.fchmod(body.fileno(), 0o600)
        json.dump(evidence, body, indent=2)
    subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(target),
            "up",
            "-d",
            "--no-build",
            "--no-deps",
            "base-backend",
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()
