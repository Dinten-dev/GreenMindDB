"""Bounded source/archive readbacks under the existing non-root copy account."""

import argparse
import json
import os
import pwd
import sqlite3
import subprocess
import time
import wave
from datetime import UTC, datetime
from pathlib import Path

from .policy import ArchiveBlocked, Recording, checksum
from .recovery import private_directory
from .wav_catalog import COLUMNS, save_json, validate_row


def main():
    import boto3
    from botocore.config import Config
    from sqlalchemy import create_engine, text

    from .health import SafetyPause
    from .runner import destination_from_environment, health_probe, source_settings
    from .storage import S3Source

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    expected = pwd.getpwnam("greenmind-raw-copy")
    if os.geteuid() != expected.pw_uid:
        raise ArchiveBlocked("Run verification as the configured greenmind-raw-copy account")
    private_directory(args.output)
    result = {
        "created_at": datetime.now(UTC).isoformat(),
        "deleted_files": 0,
        "metadata_published": False,
        "readbacks": [],
        "source_columns": {},
    }
    probe, box, clients = health_probe(), None, []

    def checkpoint():
        if not probe():
            raise SafetyPause(probe.last.get("reason", "receiver_headroom"), probe.last)

    try:
        checkpoint()
        box = destination_from_environment()
        listing = subprocess.run(  # noqa: S603 - validated, fixed SFTP adapter command
            box.command,
            input="ls -1 /home/.zfs/snapshot\n",
            text=True,
            capture_output=True,
            timeout=15,
        )
        result["snapshot_directory_accessible"] = listing.returncode == 0
        result["provider_snapshot_accepted"] = False
        for kind in COLUMNS:
            checkpoint()
            engine = create_engine(
                source_settings(kind, "production")["database"],
                pool_size=1,
                max_overflow=0,
                connect_args={
                    "connect_timeout": 3,
                    "options": "-c default_transaction_read_only=on "
                    "-c statement_timeout=3000 -c lock_timeout=150",
                },
            )
            try:
                with engine.connect() as db:
                    if db.execute(text("SHOW transaction_read_only")).scalar() != "on":
                        raise ArchiveBlocked("Verification SQL access must be read-only")
                    for table, fields in COLUMNS[kind].items():
                        result["validation_cursor"] = {"kind": kind, "table": table}
                        sample = (
                            db.execute(
                                text(
                                    "SELECT "
                                    + ",".join('"' + field + '"' for field in fields)
                                    + ' FROM "'
                                    + table
                                    + '" LIMIT 1'
                                )
                            )
                            .mappings()
                            .first()
                        )
                        if sample is not None:
                            validate_row(kind, table, dict(sample))
                    result["source_columns"][kind] = "accessible"
            finally:
                engine.dispose()
        selected = {}
        origin = Path("/var/lib/greenmind-raw-copy/archive.sqlite3")
        with sqlite3.connect(origin.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA cache_size=-1024")
            started = time.monotonic()
            db.set_progress_handler(lambda: int(time.monotonic() - started > 3), 1000)
            for (body,) in db.execute(
                "SELECT receipt FROM archive WHERE state='verified' ORDER BY rowid DESC LIMIT 5000"
            ):
                receipt = json.loads(body)
                record = Recording.from_receipt(receipt["recording"])
                if record.kind not in selected and 0 < record.size <= 1024**2:
                    if receipt["destination"] != box.identity:
                        raise ArchiveBlocked("Archive receipt names a different destination")
                    selected[record.kind] = record, receipt["remote_key"]
                if set(selected) == {"gateway", "direct"}:
                    break
        for kind in ("gateway", "direct"):
            checkpoint()
            if kind not in selected:
                raise ArchiveBlocked("Both bounded source candidates are required")
            record, key = selected[kind]
            values = source_settings(kind, "production")
            client = boto3.client(
                "s3",
                endpoint_url=values["endpoint"],
                aws_access_key_id=values["access"],
                aws_secret_access_key=values["secret"],
                config=Config(
                    connect_timeout=3,
                    read_timeout=5,
                    retries={"total_max_attempts": 1},
                    s3={"addressing_style": "path"},
                ),
            )
            clients.append(client)
            source = S3Source(client)
            original, archived = (
                args.output / (kind + "-source.wav"),
                args.output / (kind + "-archive.wav"),
            )
            source.download(record, source.snapshot(record), original)
            checkpoint()
            box.download(key, archived, record.size)
            if original.stat().st_size != record.size or archived.stat().st_size != record.size:
                raise ArchiveBlocked("Archive length differs from the original")
            if checksum(original) != record.sha256 or checksum(archived) != record.sha256:
                raise ArchiveBlocked("Archive SHA-256 differs from the original")
            with wave.open(str(archived), "rb") as audio:
                frames = audio.getnframes()
                if (
                    frames <= 0
                    or len(audio.readframes(frames))
                    != frames * audio.getnchannels() * audio.getsampwidth()
                ):
                    raise ArchiveBlocked("Archive WAV cannot be fully decoded")
            result["readbacks"].append(
                {"kind": kind, "bytes": record.size, "sha256_match": True, "full_decode": True}
            )
        result["status"] = "passed"
    except SafetyPause as error:
        result.update(status="paused", reason=error.code)
    except Exception as error:
        result.update(status="incomplete", error_type=type(error).__name__)
    finally:
        for client in clients:
            client.close()
        if box is not None:
            box.close()
        probe.close()
        save_json(args.output / "verification.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
