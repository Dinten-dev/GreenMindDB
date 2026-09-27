"""Production-only client for the isolated, fixed-command host archive bridge."""

import http.client
import json
import os
import socket


class ArchiveBridgeUnavailable(Exception):
    pass


def archive_bridge(method: str, path: str) -> dict:
    socket_path = os.environ.get("ARCHIVE_MONITOR_SOCKET", "")
    if not socket_path or (method, path) not in {("GET", "/status"), ("POST", "/copy")}:
        raise ArchiveBridgeUnavailable
    connection = http.client.HTTPConnection("localhost", timeout=3)
    try:
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(3)
        connection.sock.connect(socket_path)
        connection.request(method, path, body=b"")
        response = connection.getresponse()
        body = response.read(65537)
        if response.status != 200 or len(body) > 65536:
            raise ArchiveBridgeUnavailable
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ArchiveBridgeUnavailable
        return result
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise ArchiveBridgeUnavailable from error
    finally:
        connection.close()
