"""Two real WAVs: independent source and Storage Box readback, zero deletion."""

import json
import sqlite3
import tempfile
import time
import wave
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from .deletion import check_quarantine, eligible_after
from .health import SafetyPause
from .policy import ArchiveBlocked, Recording, checksum
from .worker import configuration


def sample(config, *, clock=lambda: datetime.now(UTC)):
    started = time.monotonic()
    with sqlite3.connect(
        (config.root / "archive.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=0.5
    ) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA cache_size=-1024")
        db.set_progress_handler(lambda: int(time.monotonic() - started > 2), 1000)
        rows = db.execute("SELECT state,receipt FROM archive ORDER BY id LIMIT 1000").fetchall()
    selected = {}
    for state, encoded in rows:
        if state != "verified":
            continue
        receipt = json.loads(encoded)
        record = Recording.from_receipt(receipt["recording"])
        if record.kind in selected or record.size > 2 * 1024**2:
            continue
        if eligible_after(record, receipt, config.local_grace_days) > clock():
            continue
        check_quarantine(config, record)
        selected[record.kind] = (record, receipt)
        if len(selected) == 2:
            break
    if set(selected) != {"gateway", "direct"}:
        raise ArchiveBlocked("Bounded sample lacks eligible Gateway and Direct receipts")
    return selected


def verify_sample(config, selected, sources, destination, checkpoint):
    results = []
    for kind, (record, receipt) in sorted(selected.items()):
        checkpoint()
        # This compares bytes only; it cannot authorize source removal.
        record.eligible(datetime.now(UTC), config, copying=True)
        if receipt["destination"] != destination.identity or (
            receipt["remote_key"] != record.remote_key(config.namespace)
        ):
            raise ArchiveBlocked("Readback receipt belongs to another destination")
        with tempfile.TemporaryDirectory(prefix="rehearsal-", dir=config.root) as folder:
            source, returned = Path(folder) / "source.wav", Path(folder) / "returned.wav"
            sources[kind].download(record, receipt["snapshot"], source)
            checkpoint()
            destination.download(receipt["remote_key"], returned, record.size)
            checkpoint()
            if any(
                path.stat().st_size != record.size or checksum(path) != record.sha256
                for path in (source, returned)
            ):
                raise ArchiveBlocked("Source/Storage Box full WAV readback mismatch")
            with wave.open(str(returned), "rb") as audio:
                frames, rate = audio.getnframes(), audio.getframerate()
                if (
                    rate <= 0
                    or frames <= 0
                    or len(audio.readframes(frames))
                    != (frames * audio.getnchannels() * audio.getsampwidth())
                ):
                    raise ArchiveBlocked("Archived WAV cannot be fully decoded")
            results.append(
                {
                    "kind": kind,
                    "archive_id": record.archive_id,
                    "bytes": record.size,
                    "sha256": record.sha256,
                    "source_readback": True,
                    "storagebox_readback": True,
                    "wav_decoded": True,
                    "sample_rate": int(rate),
                }
            )
    return results


def main():
    report = {
        "status": "blocked",
        "deleted_files": 0,
        "deletion_ready": False,
        "production_settings_changed": False,
    }
    try:
        config = configuration()
        config.guard()
        if config.delete_enabled or not config.root.is_dir():
            raise ArchiveBlocked("Readback test requires an existing copy-only journal")
        from .coordination import lease
        from .remote_lease import catalog_lease
        from .runner import destination_from_environment, health_probe, source_settings
        from .storage import S3Source

        with ExitStack() as cleanup:
            cleanup.enter_context(lease("catalog"))
            check = cleanup.enter_context(catalog_lease())
            healthy = health_probe()
            cleanup.callback(healthy.close)
            started = time.monotonic()

            def checkpoint():
                check()
                if time.monotonic() - started > 75 or not healthy():
                    raise SafetyPause("rehearsal_headroom_or_budget")

            checkpoint()
            selected = sample(config)
            destination = destination_from_environment()
            cleanup.callback(destination.close)
            import boto3
            from botocore.config import Config as BotoConfig

            sources = {}
            for kind in ("gateway", "direct"):
                values = source_settings(kind, config.namespace)
                client = boto3.client(
                    "s3",
                    endpoint_url=values["endpoint"],
                    aws_access_key_id=values["access"],
                    aws_secret_access_key=values["secret"],
                    config=BotoConfig(
                        connect_timeout=3,
                        read_timeout=8,
                        retries={"total_max_attempts": 1},
                        s3={"addressing_style": "path"},
                    ),
                )
                cleanup.callback(client.close)
                sources[kind] = S3Source(client)
            report["files"] = verify_sample(config, selected, sources, destination, checkpoint)
            report["status"] = "READBACK_TEST_PASSED"
    except Exception as error:
        report["error"] = type(error).__name__
        if isinstance(error, ArchiveBlocked):
            report["reason"] = str(error)
    print(json.dumps(report, sort_keys=True))
    return (
        0
        if report["status"] == "READBACK_TEST_PASSED"
        else 75
        if report.get("error") == "SafetyPause"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
