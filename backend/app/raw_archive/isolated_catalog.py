"""Mac verification: narrow SQL credentials, streaming export, no upload access."""

import argparse
import json
import os
import re
import stat
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from .policy import ArchiveBlocked
from .runner import metadata_source_settings
from .wav_catalog import (
    ALLOWLIST_SHA256,
    FORMAT,
    export_ledger,
    export_metadata,
    manifest_check,
    private_directory,
    save_json,
)


def load_credentials(path):
    info = path.stat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ArchiveBlocked("Private operator-owned metadata credential file required")
    values = dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    )
    permitted = {
        "RAW_ARCHIVE_GATEWAY_DATABASE_URL",
        "RAW_ARCHIVE_DIRECT_DATABASE_URL",
        "RAW_ARCHIVE_DIRECT_S3_BUCKET",
    }
    if set(values) != permitted:
        raise ArchiveBlocked("Only dedicated metadata credentials are permitted")
    for kind in ("GATEWAY", "DIRECT"):
        url = make_url(values["RAW_ARCHIVE_" + kind + "_DATABASE_URL"])
        if url.host != "127.0.0.1" or not (url.username or "").startswith(
            "greenmind_wav_metadata_"
        ):
            raise ArchiveBlocked("Use the dedicated narrow role through a loopback SSH tunnel")
    os.environ.update(values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--session", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-f0-9]{24}", args.session):
        raise ArchiveBlocked("Explicit bounded export session required")
    load_credentials(args.credentials)
    started = time.monotonic()
    last = float("-inf")

    def checkpoint():
        nonlocal last
        if time.monotonic() - started > 120:
            raise ArchiveBlocked("Production source access reached its two-minute budget")
        if time.monotonic() - last < 5:
            return
        command = [
            "ssh",
            "-i",
            str(Path.home() / ".ssh/id_ed25519"),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            "traver@188.245.247.156",
            "python3 /home/traver/greenmind-archive-acceptance-20261007/check-export-session.py "
            + args.session,
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=8)  # noqa: S603 - fixed SSH target and exactly 24 validated hexadecimal session characters
        if result.returncode or json.loads(result.stdout).get("ready") is not True:
            raise ArchiveBlocked("Production export lease or receiver reserve unavailable")
        last = time.monotonic()

    checkpoint()
    private_directory(args.output)
    namespace = "production"
    manifest = {
        "schema": 2,
        "format": FORMAT,
        "allowlist_sha256": ALLOWLIST_SHA256,
        "environment": namespace,
        "created_at": datetime.now(UTC).isoformat(),
        "files": [],
        "tables": {},
        "direct_bucket": metadata_source_settings("direct", namespace)["bucket"],
    }
    manifest["files"].extend(export_ledger(args.ledger, args.output, checkpoint))
    for kind in ("gateway", "direct"):
        engine = create_engine(
            metadata_source_settings(kind, namespace)["database"],
            pool_size=1,
            max_overflow=0,
            connect_args={
                "connect_timeout": 5,
                "options": "-c default_transaction_read_only=on -c statement_timeout=8000 -c lock_timeout=150 -c jit=off -c work_mem=2048",
            },
        )
        try:
            parts, tables = export_metadata(engine, kind, args.output, checkpoint)
            manifest["files"].extend(parts)
            manifest["tables"][kind] = tables
        finally:
            engine.dispose()
    checkpoint()
    manifest_check(manifest)
    save_json(args.output / "manifest.json", manifest)
    print(
        json.dumps(
            {
                "status": "complete",
                "tables": manifest["tables"],
                "parts": len(manifest["files"]),
                "deleted_files": 0,
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            json.dumps(
                {"status": "blocked", "error_type": type(error).__name__, "deleted_files": 0}
            )
        )
        raise SystemExit(1) from None
