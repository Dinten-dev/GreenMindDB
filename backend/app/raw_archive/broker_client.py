"""Verified reads over a private socket; no SSH credentials in public readers."""

import hashlib
import http.client
import json
import os
import re
import shutil
import socket
from pathlib import Path
from tempfile import TemporaryDirectory

from .policy import ArchiveBlocked


class Connection(http.client.HTTPConnection):
    def __init__(self, timeout=180):
        super().__init__("localhost", timeout=timeout)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(os.environ["RAW_ARCHIVE_READ_BROKER_SOCKET"])


def request(path, data, *, timeout=180):
    connection = Connection(timeout)
    try:
        connection.request("POST", path, json.dumps(data), {"Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise ArchiveBlocked("Archive read broker unavailable; retry later")
        return connection, response
    except BaseException:
        connection.close()
        raise


def authenticated_actor(authorization, access_token):
    connection = Connection(timeout=5)
    try:
        connection.request(
            "POST",
            "/authenticate",
            json.dumps({"authorization": authorization, "access_token": access_token}),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        if response.status in (401, 403):
            return response.status, None
        if response.status != 200:
            raise ArchiveBlocked("Private source authentication unavailable")
        data = response.read(1025)
        if len(data) > 1024:
            raise ArchiveBlocked("Private actor response exceeds bound")
        from uuid import UUID

        body = json.loads(data)
        if not isinstance(body, dict) or set(body) != {"id"} or not isinstance(body["id"], str):
            raise ArchiveBlocked("Invalid authenticated actor response")
        return 200, str(UUID(body["id"]))
    finally:
        connection.close()


def available(kind, bucket, keys):
    connection, response = request("/available", dict(kind=kind, bucket=bucket, keys=list(keys)))
    try:
        result = json.loads(response.read(256 * 1024 + 1))
        if not isinstance(result, list) or any(key not in keys for key in result):
            raise ArchiveBlocked("Invalid archive availability response")
        return set(result)
    finally:
        connection.close()


def restore(resources, *, kind, bucket, key, scratch):
    connection, response = request("/read", dict(kind=kind, bucket=bucket, key=key))
    try:
        size = int(response.getheader("Content-Length", "0"))
        digest = response.getheader("X-Content-SHA256", "")
        if not 0 < size <= 64 * 1024**2 or not re.fullmatch("[0-9a-f]{64}", digest):
            raise ArchiveBlocked("Invalid archive read response")
        if shutil.disk_usage(scratch).free < 2 * 1024**3 + size:
            raise ArchiveBlocked("Insufficient archive read scratch space")
        folder = resources.enter_context(TemporaryDirectory(prefix="broker-", dir=scratch))
        path = Path(folder) / "verified.wav"
        actual, count = hashlib.sha256(), 0
        with path.open("xb") as output:
            while block := response.read(64 * 1024):
                count += len(block)
                if count > size:
                    raise ArchiveBlocked("Archive read exceeds declared size")
                actual.update(block)
                output.write(block)
        if count != size or actual.hexdigest() != digest:
            raise ArchiveBlocked("Archive broker readback mismatch")
        return path
    finally:
        connection.close()
