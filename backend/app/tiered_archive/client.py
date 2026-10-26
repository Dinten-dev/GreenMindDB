"""Private historical reads through an independently bounded archive service."""

import http.client
import json
import os
import socket
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked


class Connection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def request(path, data, *, timeout=30):
    if path not in {"/tiered-series", "/tiered-features", "/tiered-revision"}:
        raise ArchiveBlocked("Historical broker operation not allowed")
    name = os.environ.get("TIERED_ARCHIVE_READ_BROKER_SOCKET") or os.environ.get(
        "RAW_ARCHIVE_READ_BROKER_SOCKET"
    )
    if not name or not Path(name).is_absolute():
        raise ArchiveBlocked("Private historical socket required")
    connection = Connection(name, min(timeout, 30))
    try:
        connection.request("POST", path, json.dumps(data), {"Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise ArchiveBlocked("Historical archive temporarily unavailable")
        return connection, response
    except BaseException:
        connection.close()
        raise
