"""Prepare an isolated 8141 read candidate. No start, reload or receiver changes."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from app.raw_archive.compat_proxy import draft, rollback

RUNTIME = Path(__file__).resolve().parents[3]
LIVE = Path("/home/traver/greenmind-archive-production/33fe395")


def main():
    assert os.geteuid() == 0
    bundle = json.loads((RUNTIME / "bundle.json").read_text())
    revision = bundle["revision"]
    state = (
        Path("/mnt/HC_Volume_106755700/greenmind-delete-preparation") / revision[:12]
    )
    target = state / "reader"
    assert not target.exists()
    target.mkdir(mode=0o700)
    original = Path("/etc/nginx/sites-available/greenmind-prod").read_text()
    sha = hashlib.sha256(original.encode()).hexdigest()
    assert sha == bundle["proxy_before_sha256"]
    candidate = draft(original, sha, reader_port=8141)
    assert rollback(candidate, reader_port=8141) == original
    values = dict(
        line.split("=", 1)
        for line in (LIVE / "read.env").read_text().splitlines()
        if "=" in line
    )
    assert not any(key.startswith("RAW_ARCHIVE_SFTP_") for key in values)
    for flag in (
        "RAW_ARCHIVE_DELETE_ENABLED",
        "RAW_ARCHIVE_READS_ACCEPTED",
        "RETENTION_ENABLED",
        "DIRECT_RETENTION_ENABLED",
        "DIRECT_INGEST_ENABLED",
        "VISUAL_PRUNE_ENABLED",
    ):
        assert values[flag] == "false"
    assert values["S3_READ_ONLY"] == "true"
    values.update(ARCHIVE_COMPAT_READS_ENABLED="true", RELEASE_REVISION=revision)
    with (target / "read.env").open("x") as body:
        os.chmod(body.name, 0o600)
        body.write("".join(key + "=" + value + "\n" for key, value in values.items()))
    current = json.loads((LIVE / "compose.json").read_text())
    api = current["services"]["api"]
    api["env_file"] = [str(target / "read.env")]
    api["ports"] = ["127.0.0.1:8141:8000"]
    api["restart"] = "no"
    api["cpus"] = 0.10
    api["volumes"].append(str(RUNTIME / "backend/app") + ":/app/app:ro")
    config = {
        "name": "gm-archive-compat-" + revision[:12],
        "services": {"api": api},
        "networks": current["networks"],
    }
    (target / "compose.json").write_text(json.dumps(config, indent=2) + "\n")
    for name, text in [("before.conf", original), ("candidate.conf", candidate)]:
        path = target / name
        path.write_text(text)
        path.chmod(0o600)
    # Enrollment preserves stable physical sensor names in real catalog recovery.
    database = urlsplit(values["DIRECT_DATABASE_URL"]).path.lstrip("/")
    role = urlsplit(values["DIRECT_DATABASE_URL"]).username
    assert re.fullmatch(r"[a-zA-Z0-9_]+", database)
    assert role == "greenmind_archive_reader_33fe395"
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "greenminddb-postgres-1",
            "psql",
            "-U",
            "admin",
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input="SET lock_timeout='150ms'; SET statement_timeout='3s'; "
        "GRANT SELECT (hardware_id,device_id,created_at) ON direct_enrollment TO "
        + role
        + ";\n",
        text=True,
        capture_output=True,
        timeout=8,
    )
    assert result.returncode == 0, (
        "Exact enrollment SELECT grant failed; private output withheld"
    )
    manifest = {
        "revision": revision,
        "runtime": str(RUNTIME),
        "source_image": api["image"],
        "source_bind_read_only": True,
        "before_sha256": sha,
        "candidate_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
        "reader_port": 8141,
        "started": False,
        "proxy_changed": False,
        "deleted_files": 0,
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
