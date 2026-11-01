"""Private file handshake with the existing, manually supervised Mac admin operator.

No credential, network access or new daemon. This is a one-off backup operator
adapter, not an unattended production transport.
"""

import hashlib
import json
import os
import time
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked

from .codec import digest
from .source_schema import private_directory, private_read


class AdminResponseHandoff:
    """Expose one metadata-only request; accept only the exact private response."""

    def __init__(self, directory, *, timeout_seconds=65):
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 65:
            raise ArchiveBlocked("Bounded private schema handoff required")
        self.directory = Path(directory)
        self.timeout_seconds = timeout_seconds

    def __call__(self, request, *, checkpoint):
        root = private_directory(self.directory)
        if os.geteuid() != 0 or set(root.iterdir()):
            raise ArchiveBlocked("New empty Root-private schema handoff required")
        raw = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        if len(raw) > 32768:
            raise ArchiveBlocked("Bounded schema request required")
        fd = os.open(
            root / "request.pending", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(root / "request.pending", root / "request.json")
        began = time.monotonic()
        while time.monotonic() - began < self.timeout_seconds:
            checkpoint()
            if (root / "response.json").exists():
                response = private_read(root / "response.json", 32768)
                item = json.loads(response)
                if item.get("request_sha256") != digest(request):
                    raise ArchiveBlocked("Private schema response belongs to another request")
                return root, hashlib.sha256(response).hexdigest()
            time.sleep(0.1)
        raise ArchiveBlocked("Private source schema operator did not complete")
