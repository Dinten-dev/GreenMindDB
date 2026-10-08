"""Private host-only read broker. Exposes no upload, delete or command API."""

import fcntl
import hashlib
import json
import os
import socket
import socketserver
import sqlite3
import stat
import struct
from contextlib import closing
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from threading import BoundedSemaphore
from urllib.request import urlopen

from .policy import ArchiveBlocked, checksum
from .restore import archived_file
from .worker import configuration


def validate(data, *, listing=False):
    if not isinstance(data, dict) or set(data) != {"kind", "bucket", "keys" if listing else "key"}:
        raise ArchiveBlocked("Invalid request")
    kind, bucket = data["kind"], data["bucket"]
    if kind not in {"gateway", "direct"} or bucket != (
        "greenmind-raw" if kind == "gateway" else os.environ.get("RAW_ARCHIVE_DIRECT_S3_BUCKET")
    ):
        raise ArchiveBlocked("Invalid source")
    keys = data["keys"] if listing else [data["key"]]
    if not isinstance(keys, list) or not 1 <= len(keys) <= 1000:
        raise ArchiveBlocked("Invalid key count")
    if any(
        not isinstance(key, str)
        or len(key) > 1024
        or not key.endswith(".wav")
        or any(ord(c) < 32 for c in key)
        for key in keys
    ):
        raise ArchiveBlocked("Invalid key")
    return kind, bucket, keys


def available(config, kind, bucket, keys):
    result = []
    with closing(
        sqlite3.connect(
            (config.root / "archive.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=2
        )
    ) as db:
        db.execute("PRAGMA query_only=ON")
        for key in keys:
            identity = hashlib.sha256(
                json.dumps([kind, bucket, key], separators=(",", ":")).encode()
            ).hexdigest()
            row = db.execute("SELECT state FROM archive WHERE id=?", (identity,)).fetchone()
            if row and row[0] in {"verified", "deleting", "evicted"}:
                result.append(key)
    return result


def healthy():
    memory = next(
        int(line.split()[1])
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    if memory < 512 * 1024 or os.getloadavg()[0] > 2.4:
        raise ArchiveBlocked("Preserve receiver headroom")
    for port in (8120, 8003, 8000):
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            if response.status != 200:
                raise ArchiveBlocked("Receiver unavailable")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Object identities and credentials must never enter access logs.

    def do_POST(self):
        self.connection.settimeout(10)
        acquired = False
        try:
            if hasattr(socket, "SO_PEERCRED"):
                _, uid, _ = struct.unpack(
                    "3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                )
                if uid != os.getuid():
                    raise ArchiveBlocked("Unauthorized peer")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1024 * 1024 or self.headers.get("Transfer-Encoding"):
                raise ArchiveBlocked("Invalid body")
            data = json.loads(self.rfile.read(size))
            if self.path not in {"/available", "/read"}:
                raise ArchiveBlocked("Unsupported operation")
            kind, bucket, keys = validate(data, listing=self.path == "/available")
            if self.path == "/available":
                payload = json.dumps(available(self.server.config, kind, bucket, keys)).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            acquired = self.server.read_slot.acquire(blocking=False)
            if not acquired:
                raise ArchiveBlocked("Read concurrency exhausted")
            healthy()
            with archived_file(
                self.server.config,
                self.server.destination,
                kind=kind,
                bucket=bucket,
                key=keys[0],
                scratch=self.server.scratch,
            ) as path:
                healthy()
                self.send_response(200)
                self.send_header("Content-Length", str(path.stat().st_size))
                self.send_header("X-Content-SHA256", checksum(path))
                self.end_headers()
                with path.open("rb") as source:
                    while block := source.read(64 * 1024):
                        self.wfile.write(block)
        except Exception:
            # No exception text: downstream SSH/SQLite errors may contain private paths.
            try:
                self.send_error(503, "Verified archive read unavailable")
            except OSError:
                pass
        finally:
            if acquired:
                self.server.read_slot.release()


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 4

    def handle_error(self, request, address):
        pass  # Do not log request identities or private storage exceptions.

    def __init__(self, path, config, destination, scratch):
        self.config, self.destination, self.scratch = config, destination, scratch
        self.read_slot = BoundedSemaphore(1)
        self.connections = BoundedSemaphore(4)
        super().__init__(path, Handler)

    def process_request(self, request, address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.connections.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.connections.release()


def main():
    from .storage import StorageBox

    config = configuration()
    config.guard(reading=True)
    if config.delete_enabled:
        raise ArchiveBlocked("Read broker never enables deletion")
    from .read_only_box import configured_read_only_box

    destination = configured_read_only_box() or StorageBox(
        host=os.environ["RAW_ARCHIVE_SFTP_HOST"],
        user=os.environ["RAW_ARCHIVE_SFTP_USER"],
        key=Path(os.environ["RAW_ARCHIVE_SFTP_KEY"]),
        known_hosts=Path(os.environ["RAW_ARCHIVE_SFTP_KNOWN_HOSTS"]),
        port=int(os.environ.get("RAW_ARCHIVE_SFTP_PORT", "23")),
        root=os.environ.get("RAW_ARCHIVE_SFTP_ROOT", "greenmind-raw"),
        kilobits=2048,
    )
    scratch = Path(os.environ["RAW_ARCHIVE_READ_SCRATCH_DIR"])
    path = Path(os.environ["RAW_ARCHIVE_READ_BROKER_SOCKET"])
    if not scratch.is_dir() or scratch.is_symlink() or scratch.stat().st_mode & 0o077:
        raise ArchiveBlocked("Private fresh socket and scratch required")
    os.umask(0o077)
    with (path.parent / "broker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists():
            if not stat.S_ISSOCK(path.lstat().st_mode) or path.lstat().st_uid != os.getuid():
                raise ArchiveBlocked("Unexpected socket path")
            path.unlink()  # Only our stale IPC socket, never an original or archive file.
        with Server(str(path), config, destination, scratch) as server:
            server.serve_forever(poll_interval=1)


if __name__ == "__main__":
    main()
