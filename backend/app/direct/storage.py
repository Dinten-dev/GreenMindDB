"""Direct-only content-addressed artifacts. Uploads are read back and verified."""

import hashlib
import os
import re
import tempfile

import boto3
from botocore.config import Config

KEY_RE = re.compile(r"^direct/[0-9a-f-]{36}/[0-9a-f-]{36}/[0-9a-f]{64}\.wav$")


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

    def put(self, device_id, session_id, payload, *, identity):
        digest = hashlib.sha256(payload).hexdigest()
        object_id = hashlib.sha256(identity.encode() + b"\0" + payload).hexdigest()
        key = f"direct/{device_id}/{session_id}/{object_id}.wav"
        path = self._path(key)
        if self.client:
            self.client.put_object(
                Bucket=self.settings.s3_bucket,
                Key=key,
                Body=payload,
                ContentType="audio/wav",
                Metadata={"sha256": digest},
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
        if hashlib.sha256(self.get(key)).hexdigest() != digest:
            raise OSError("Direct artifact verification failed")
        return key, digest

    def get(self, key):
        path = self._path(key)
        if self.client:
            response = self.client.get_object(Bucket=self.settings.s3_bucket, Key=key)
            try:
                value = response["Body"].read(self.settings.max_segment_bytes + 4097)
            finally:
                response["Body"].close()
        else:
            with path.open("rb") as source:
                value = source.read(self.settings.max_segment_bytes + 4097)
        if len(value) > self.settings.max_segment_bytes + 4096:
            raise OSError("Direct artifact exceeds bounded read size")
        return value

    def delete(self, key):
        path = self._path(key)
        if self.client:
            self.client.delete_object(Bucket=self.settings.s3_bucket, Key=key)
        else:
            path.unlink(missing_ok=True)
