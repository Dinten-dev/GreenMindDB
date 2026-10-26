"""One explicit WAV readback; never repairs metadata or removes originals."""

import argparse
import json
import os
import uuid
import wave
from datetime import UTC, datetime
from pathlib import Path

from .health import SafetyPause
from .policy import ArchiveBlocked, checksum
from .recovery import private_directory


def inspect(wav_id, output):
    import boto3
    import psycopg2
    from botocore.config import Config

    from .runner import health_probe, source_settings

    probe = health_probe()
    client = None

    def checkpoint():
        if not probe():
            raise SafetyPause(probe.last["reason"], probe.last)

    try:
        checkpoint()
        private_directory(output)
        values = source_settings("gateway", "production")
        db = psycopg2.connect(
            values["database"].replace("postgresql+psycopg2:", "postgresql:"),
            connect_timeout=3,
            options="-c default_transaction_read_only=on "
            "-c statement_timeout=3000 -c lock_timeout=150 -c jit=off",
        )
        try:
            with db, db.cursor() as cursor:
                cursor.execute("SHOW transaction_read_only")
                if cursor.fetchone()[0] != "on":
                    raise ArchiveBlocked("Investigation source must be read-only")
                cursor.execute(
                    "SELECT id,s3_key,content_sha256,file_size_bytes,feature_status,"
                    "raw_deleted_at FROM wav_file WHERE id=%s",
                    (str(wav_id),),
                )
                row = cursor.fetchone()
                if row is None or row[5] is not None or not 0 < row[3] <= 1024**2:
                    raise ArchiveBlocked("Explicit present WAV up to one MiB required")
        finally:
            db.close()
        checkpoint()
        client = boto3.client(
            "s3",
            endpoint_url=values["endpoint"],
            aws_access_key_id=values["access"],
            aws_secret_access_key=values["secret"],
            config=Config(connect_timeout=3, read_timeout=8, retries={"total_max_attempts": 1}),
        )
        head = client.head_object(Bucket=values["bucket"], Key=row[1])
        if head["ContentLength"] != row[3]:
            raise ArchiveBlocked("WAV source length differs from metadata")
        arguments = {"Bucket": values["bucket"], "Key": row[1], "IfMatch": head["ETag"]}
        if head.get("VersionId") is not None:
            arguments["VersionId"] = head["VersionId"]
        response = client.get_object(**arguments)
        path, size = output / "original-readback.wav", 0
        try:
            with path.open("xb") as body:
                os.chmod(path, 0o600)
                while block := response["Body"].read(64 * 1024):
                    checkpoint()
                    size += len(block)
                    if size > row[3]:
                        raise ArchiveBlocked("Source exceeded declared length")
                    body.write(block)
        finally:
            response["Body"].close()
        if size != row[3]:
            raise ArchiveBlocked("Incomplete source readback")
        after = client.head_object(Bucket=values["bucket"], Key=row[1])
        if any(
            head.get(key) != after.get(key)
            for key in ("ETag", "VersionId", "ContentLength", "LastModified")
        ):
            raise ArchiveBlocked("Original changed during investigation")
        digest = checksum(path)
        try:
            with wave.open(str(path), "rb") as audio:
                frames = audio.getnframes()
                width, channels = audio.getsampwidth(), audio.getnchannels()
                audio_info = {
                    "frames": frames,
                    "sample_rate": audio.getframerate(),
                    "sample_bits": width * 8,
                    "channels": channels,
                    "complete": len(audio.readframes(frames)) == frames * width * channels,
                }
        except (wave.Error, EOFError):
            audio_info = {"complete": False}
        report = {
            "at": datetime.now(UTC).isoformat(),
            "identity": str(wav_id),
            "expected_sha256": row[2],
            "actual_sha256": digest,
            "bytes": size,
            "checksum_matches_metadata": digest == row[2],
            "feature_status": row[4],
            "audio": audio_info,
            "quarantine_retained": True,
            "source_modified": False,
            "deleted_files": 0,
        }
        with (output / "investigation.json").open("x") as body:
            os.chmod(body.name, 0o600)
            json.dump(report, body, indent=2)
        print(json.dumps(report))
    finally:
        probe.close()
        if client:
            client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-wav", type=uuid.UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        inspect(args.gateway_wav, args.output)
    except SafetyPause as error:
        print(
            json.dumps(
                {
                    "status": "paused",
                    "reason": error.code,
                    "details": error.details,
                    "deleted_files": 0,
                    "complete": False,
                }
            )
        )
        raise SystemExit(75) from None


if __name__ == "__main__":
    main()
