"""Bounded forced-command SSH lease shares the production copier's actual lock."""

import argparse
import json
import os
import re
import select
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .coordination import lease
from .health import SafetyPause
from .policy import ArchiveBlocked

PREFIX = "RAW_ARCHIVE_LEASE_"
SOURCE_PAUSES = {
    "source_memory",
    "source_load",
    "source_receiver_health",
    "archive_job_busy",
    "source_lease_blocked",
}


def source_health():
    """Only inspect local production resources and fixed receiver routes."""
    from urllib.request import urlopen

    available = (
        next(
            int(line.split()[1])
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")
        )
        / 1024
    )
    if available < 640:
        raise SafetyPause("source_memory")
    if os.getloadavg()[0] > 2.4:
        raise SafetyPause("source_load")
    from .source_probe import gateway_ports

    for port in (*gateway_ports(), 8003, 8000):
        try:
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                if response.status != 200:
                    raise SafetyPause("source_receiver_health")
        except OSError:
            raise SafetyPause("source_receiver_health") from None


def serve(input_stream, output_stream, *, healthy=source_health, clock=time.monotonic):
    """One catalog lease, at most 120 seconds; silence/disconnect releases it."""
    if os.environ.get("SSH_ORIGINAL_COMMAND") != "catalog":
        raise ArchiveBlocked("Only the fixed catalog operation is permitted")
    if os.uname().nodename != "Biolingo":
        raise ArchiveBlocked("Only the production lock authority may serve leases")
    import secrets

    def send(body):
        output_stream.write(json.dumps(body) + "\n")
        output_stream.flush()

    with lease("catalog"):
        healthy()
        session = secrets.token_hex(16)
        deadline = clock() + 120
        send({"ready": True, "session": session, "maximum_seconds": 120})
        pending = bytearray()
        while clock() < deadline:
            if b"\n" not in pending:
                remaining = deadline - clock()
                if (
                    remaining <= 0
                    or not select.select([input_stream], [], [], min(6, remaining))[0]
                ):
                    return
                # readline() could block forever on a partial line after select.
                # Read only already-available pipe bytes and recheck the deadline.
                chunk = os.read(input_stream.fileno(), 1025 - len(pending))
                if not chunk:
                    return
                pending.extend(chunk)
            if len(pending) > 1024:
                raise ArchiveBlocked("Lease request exceeds its bound")
            if b"\n" not in pending:
                continue
            raw, _, remainder = pending.partition(b"\n")
            pending = bytearray(remainder)
            value = json.loads(raw)
            if value != {"session": session, "action": "renew"}:
                raise ArchiveBlocked("Lease session or action differs")
            healthy()
            if clock() >= deadline:
                return
            send({"ready": True, "session": session})


def private_file(value):
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ArchiveBlocked("Explicit private lease transport files required")
    if path.stat().st_mode & 0o077:
        raise ArchiveBlocked("Lease transport files must be private")
    return path


class RemoteLease:
    def __init__(self, command, *, popen=subprocess.Popen):
        self.command, self.popen = command, popen
        self.process = None
        self.thread = None
        self.stop = threading.Event()
        self.failure = None

    def response(self):
        if not select.select([self.process.stdout], [], [], 8)[0]:
            raise SafetyPause("source_lease_timeout")
        raw = self.process.stdout.readline(1025)
        if not raw or len(raw) > 1024:
            raise SafetyPause("source_lease_lost")
        try:
            value = json.loads(raw)
        except ValueError:
            raise SafetyPause("source_lease_invalid") from None
        if value.get("ready") is not True:
            reason = value.get("reason")
            raise SafetyPause(
                reason
                if isinstance(reason, str) and reason in SOURCE_PAUSES
                else "source_lease_unavailable"
            )
        if not re.fullmatch(r"[0-9a-f]{32}", value.get("session", "")):
            raise SafetyPause("source_lease_invalid")
        return value

    def __enter__(self):
        # Stop new work early enough for an eight-second SQL statement to finish
        # before the authority's absolute two-minute expiry. Transport never
        # extends this conservative client deadline.
        self.deadline = time.monotonic() + 110
        self.process = self.popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        try:
            initial = self.response()
            if initial.get("maximum_seconds") != 120:
                raise SafetyPause("source_lease_invalid")
            self.session = initial["session"]
            self.thread = threading.Thread(target=self.renew, daemon=True)
            self.thread.start()
            return self
        except BaseException:
            self.close()
            raise

    def renew(self):
        while not self.stop.wait(2):
            try:
                self.process.stdin.write(
                    json.dumps({"session": self.session, "action": "renew"}) + "\n"
                )
                self.process.stdin.flush()
                if self.response()["session"] != self.session:
                    raise SafetyPause("source_lease_invalid")
            except SafetyPause as error:
                self.failure = error.code
                return
            except Exception:
                self.failure = "source_lease_lost"
                return

    def check(self):
        if time.monotonic() >= self.deadline:
            raise SafetyPause("source_lease_deadline")
        if self.failure or self.process.poll() is not None:
            raise SafetyPause(self.failure or "source_lease_lost")

    def close(self):
        self.stop.set()
        if self.process is not None:
            if self.process.stdin is not None:
                try:
                    self.process.stdin.close()
                except OSError:
                    pass
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            if self.process.stdout is not None:
                self.process.stdout.close()
        if self.thread is not None:
            self.thread.join(timeout=9)

    def __exit__(self, *_):
        self.close()


@contextmanager
def catalog_lease():
    host = os.environ.get(PREFIX + "HOST")
    if not host:
        if os.environ.get("RAW_ARCHIVE_WORKER_ROLE") == "archive":
            raise ArchiveBlocked("Archive-host exports require the actual source lease")
        with lease("catalog"):
            yield lambda: None
        return
    if host != "188.245.247.156" or os.environ.get(PREFIX + "USER") != "greenmind-archive-lease":
        raise ArchiveBlocked("Only the configured production lease authority is permitted")
    key = private_file(os.environ[PREFIX + "KEY"])
    known = private_file(os.environ[PREFIX + "KNOWN_HOSTS"])
    command = [
        "/usr/bin/ssh",
        "-F",
        "/dev/null",
        "-T",
        "-i",
        str(key),
        "-o",
        "UserKnownHostsFile=" + str(known),
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=1",
        "greenmind-archive-lease@" + host,
        "catalog",
    ]
    with RemoteLease(command) as remote:
        yield remote.check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", required=True)
    parser.parse_args()
    os.environ["RAW_ARCHIVE_COORDINATION_DIR"] = "/var/lib/greenmind-archive-coordination"
    try:
        serve(sys.stdin, sys.stdout)
        return 0
    except Exception as error:
        reason = error.code if isinstance(error, SafetyPause) else "source_lease_blocked"
        print(
            json.dumps({"ready": False, "reason": reason, "error_type": type(error).__name__}),
            flush=True,
        )
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
