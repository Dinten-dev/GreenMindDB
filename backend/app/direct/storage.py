"""Direct-only content-addressed artifacts. Uploads are read back and verified."""

import hashlib
import os
import re
import tempfile
from datetime import UTC, datetime

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.raw_archive import reader as archive_reader
from app.raw_archive.worklease import shared_wav_lease

KEY_RE = re.compile(
    r"^direct/[0-9a-f-]{36}/[0-9a-f-]{36}/(?:"
    r"[0-9a-f]{64}|[0-9]{8}T[0-9]{9}Z_e[0-9]+_b[0-9]+_q[0-9]+_f[0-9]+_[0-9a-f]{64})\.wav$"
)


def utc_stamp(started_at_us):
    epoch_ms = started_at_us // 1000
    seconds, ms = divmod(epoch_ms, 1000)
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y%m%dT%H%M%S") + f"{ms:03d}Z", epoch_ms


class ArtifactStore:
    def __init__(self, settings):
        self.settings = settings
        self.client = None
        if settings.storage == "s3":
            if not settings.s3_bucket.startswith("greenmind-direct-"):
                raise ValueError("Direct cannot write to a legacy bucket")
            self.client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=settings.s3_access_key.get_secret_value(),
                aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
                config=Config(
                    connect_timeout=5,
                    read_timeout=30,
                    retries={"max_attempts": 2},
                    s3={"addressing_style": "path"},
                ),
            )

    def _path(self, key):
        if not KEY_RE.fullmatch(key):
            raise ValueError("Unsafe artifact key")
        root = self.settings.artifact_root.resolve()
        path = (root / key).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Artifact escapes Direct storage")
        return path

    def put(
        self, device_id, session_id, payload, *, identity, capture_assignment=None, naming=None
    ):
        metadata = {}
        if capture_assignment is not None:
            from app.direct.capture_assignment import validate

            capture = validate(capture_assignment, device_id, session_id)
            metadata = {
                "capture-assignment-sha256": capture["sha256"],
                "capture-organization-id": capture["organization_id"],
                "capture-version": "1",
            }
            identity += "\0capture:" + capture["sha256"]
        digest = hashlib.sha256(payload).hexdigest()
        object_id = hashlib.sha256(identity.encode() + b"\0" + payload).hexdigest()
        filename = object_id
        if self.settings.timestamp_object_keys_enabled and naming is not None:
            stamp, epoch_ms = utc_stamp(naming["started_at_us"])
            filename = (
                f"{stamp}_e{epoch_ms}_b{naming['boot_counter']}"
                f"_q{naming['sequence']}_f{naming['first_frame']}_{object_id}"
            )
        key = f"direct/{device_id}/{session_id}/{filename}.wav"
        path = self._path(key)
        if self.client:
            self.client.put_object(
                Bucket=self.settings.s3_bucket,
                Key=key,
                Body=payload,
                ContentType="audio/wav",
                Metadata={"sha256": digest, **metadata},
            )
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".direct-")
            try:
                with os.fdopen(fd, "wb") as target:
                    target.write(payload)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, path)
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        if hashlib.sha256(self.get_local(key)).hexdigest() != digest:
            raise OSError("Direct artifact verification failed")
        return key, digest

    def get(self, key):
        with shared_wav_lease("direct", self.settings.s3_bucket, key):
            return self._get(key)

    def _get(self, key):
        # Ordinary readers retain the existing archive-aware S3 path. Source
        # verification must use get_local, which never calls this fallback.
        if self.client and self.settings.archive_broker_socket is None:
            self._path(key)
            return self._read_response(
                archive_reader.get_object(
                    self.client, Bucket=self.settings.s3_bucket, Key=key, kind="direct"
                )
            )
        try:
            return self.get_local(key)
        except FileNotFoundError:
            if self.settings.archive_broker_socket is None:
                raise
            return self.get_archive(key)
        except ClientError as error:
            if self.settings.archive_broker_socket is None or error.response["Error"][
                "Code"
            ] not in {"NoSuchKey", "NoSuchVersion", "404"}:
                raise
            return self.get_archive(key)

    def get_archive(self, key):
        with shared_wav_lease("direct", self.settings.s3_bucket, key):
            return self._get_archive(key)

    def _get_archive(self, key):
        from app.direct.archive_reader import read_archive

        self._path(key)
        return read_archive(self.settings, key)

    def archive_available(self, keys):
        from app.direct.archive_reader import available_archive

        for key in keys:
            self._path(key)
        return available_archive(self.settings, keys)

    def get_local(self, key):
        with shared_wav_lease("direct", self.settings.s3_bucket, key):
            return self._get_local(key)

    def _get_local(self, key):
        path = self._path(key)
        if self.client:
            return self._read_response(
                self.client.get_object(Bucket=self.settings.s3_bucket, Key=key)
            )
        else:
            with path.open("rb") as source:
                value = source.read(self.settings.max_segment_bytes + 4097)
        if len(value) > self.settings.max_segment_bytes + 4096:
            raise OSError("Direct artifact exceeds bounded read size")
        return value

    def _read_response(self, response):
        try:
            value = response["Body"].read(self.settings.max_segment_bytes + 4097)
        finally:
            response["Body"].close()
        if len(value) > self.settings.max_segment_bytes + 4096:
            raise OSError("Direct artifact exceeds bounded read size")
        return value

    def delete(self, key):
        path = self._path(key)
        if self.client:
            self.client.delete_object(Bucket=self.settings.s3_bucket, Key=key)
        else:
            path.unlink(missing_ok=True)
