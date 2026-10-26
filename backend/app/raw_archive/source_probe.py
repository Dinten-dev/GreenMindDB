"""Authenticated, loopback-only source telemetry; no command or write endpoint."""

import hmac
import json
import os
import re
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.request import urlopen

GATEWAY_CONFIG = Path("/etc/greenmind/receiver-health/gateway.json")


def gateway_ports(path=GATEWAY_CONFIG):
    """Root-pinned transition routes; missing configuration retains the original."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return (8120,)
    if path.is_symlink() or not path.is_file() or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError("Root-owned receiver configuration required")
    if any(
        parent.is_symlink() or parent.stat().st_uid != 0 or parent.stat().st_mode & 0o022
        for parent in path.parents
    ):
        raise ValueError("Root-owned immutable receiver configuration ancestry required")
    if info.st_size > 1024:
        raise ValueError("Receiver configuration exceeds its bound")
    value = json.loads(path.read_text())
    if value not in ({"schema": 1, "ports": [8120]}, {"schema": 1, "ports": [8120, 8152]}):
        raise ValueError("Only the original and reviewed transition receiver permitted")
    return tuple(value["ports"])


def snapshot(mounts):
    memory = next(
        int(line.split()[1])
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    routes = {}
    actual = (*gateway_ports(), 8003, 8000)
    for port in actual:
        try:
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                routes[str(port)] = response.status
        except Exception:
            routes[str(port)] = 0
    gateway = {str(port): routes[str(port)] for port in actual[:-2]}
    routes = {
        "8120": 200 if all(status == 200 for status in gateway.values()) else 0,
        "8003": routes["8003"],
        "8000": routes["8000"],
    }
    return {
        "schema": 1,
        "checked_at": time.time(),
        "source": os.environ["GREENMIND_SOURCE_ID"],
        "available_mib": memory / 1024,
        "load": os.getloadavg()[0],
        "routes": routes,
        "gateway_receivers": gateway,
        "mounts": [
            {"total": usage.total, "free": usage.free} for usage in map(shutil.disk_usage, mounts)
        ],
    }


class Server(HTTPServer):
    allow_reuse_address = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.connection.settimeout(3)
        if self.path != "/source-health" or not hmac.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + self.server.token
        ):
            self.send_error(404)
            return
        try:
            with self.server.lock:
                now = time.monotonic()
                if now - self.server.last >= 2:
                    self.server.cached = snapshot(self.server.mounts)
                    self.server.last = now
                value = dict(self.server.cached)
                nonce = self.headers.get("X-GreenMind-Probe-Nonce", "")
                if re.fullmatch(r"[0-9a-f]{32}", nonce):
                    value["probe_nonce"] = nonce
                    value["sample_age_seconds"] = time.monotonic() - self.server.last
                raw = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(raw)
        except Exception:
            self.send_error(503)


def main():
    token_path = Path(os.environ["GREENMIND_SOURCE_PROBE_TOKEN_FILE"])
    info = token_path.stat()
    if token_path.is_symlink() or info.st_mode & 0o007:
        raise ValueError("Private source-probe token required")
    token = token_path.read_text().strip()
    if len(token) < 32:
        raise ValueError("Bounded source-probe authentication required")
    mounts = [Path(value) for value in os.environ["RAW_ARCHIVE_SOURCE_MOUNTS"].split(",")]
    if not mounts or any(not value.is_absolute() or not value.is_dir() for value in mounts):
        raise ValueError("Explicit source mounts required")
    server = Server(("127.0.0.1", 8150), Handler)
    server.token, server.mounts = token, mounts
    server.last, server.cached, server.lock = float("-inf"), None, threading.Lock()
    server.serve_forever()


if __name__ == "__main__":
    main()
