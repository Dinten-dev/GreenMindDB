"""Read-only access to the private verified Storage Box broker."""

import hashlib
import http.client
import json
import re
import socket


class BrokerConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=30)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def read_archive(settings, key):
    if settings.archive_broker_socket is None:
        raise OSError("Verified archive broker is not configured")
    connection = BrokerConnection(settings.archive_broker_socket)
    try:
        connection.request(
            "POST",
            "/read",
            json.dumps(dict(kind="direct", bucket=settings.s3_bucket, key=key)),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        if response.status != 200:
            raise OSError("Verified archive unavailable")
        size = int(response.getheader("Content-Length", "0"))
        digest = response.getheader("X-Content-SHA256", "")
        if not 0 < size <= settings.max_segment_bytes + 4096 or not re.fullmatch(
            "[0-9a-f]{64}", digest
        ):
            raise OSError("Invalid archive response")
        data = response.read(size + 1)
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise OSError("Archive readback mismatch")
        return data
    finally:
        connection.close()


def available_archive(settings, keys):
    if settings.archive_broker_socket is None or not 1 <= len(keys) <= 1000:
        raise OSError("Verified archive availability is not configured or exceeds bound")
    connection = BrokerConnection(settings.archive_broker_socket)
    try:
        connection.request(
            "POST",
            "/available",
            json.dumps(dict(kind="direct", bucket=settings.s3_bucket, keys=sorted(keys))),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        if response.status != 200:
            raise OSError("Verified archive availability unavailable")
        data = response.read(256 * 1024 + 1)
        if len(data) > 256 * 1024:
            raise OSError("Archive availability exceeds bound")
        result = json.loads(data)
        if not isinstance(result, list) or any(
            not isinstance(k, str) or k not in keys for k in result
        ):
            raise OSError("Invalid archive availability response")
        return set(result)
    finally:
        connection.close()
