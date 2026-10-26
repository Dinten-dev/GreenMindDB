"""Bounded private coordinator; no human terminal lifetime is required.

Independent source watchdog survives this process and confirms target fencing
through its dedicated restricted key before any source writer is resumed.
"""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

CONTROL = "/opt/greenmind/staging-cutover-20261010/control.py"


class Coordinator:
    def __init__(self, known, state):
        self.known, self.state = known, state
        self.cohort = secrets.token_hex(12)
        self.started = time.monotonic()
        self.armed = False
        state.mkdir(mode=0o700)

    def command(self, host):
        return [
            "ssh",
            "-F",
            "/dev/null",
            "-T",
            "-i",
            str(Path.home() / ".ssh/id_ed25519"),
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
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UserKnownHostsFile=" + str(self.known),
            "traver@" + host,
            "sudo -n /usr/bin/python3 " + CONTROL,
        ]

    def check(self):
        if time.monotonic() - self.started > 650:
            raise TimeoutError("Coordinator budget")

    def record(self, name, value):
        with (self.state / name).open("x") as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(value, f)
            f.flush()
            os.fsync(f.fileno())

    def call(self, host, action, **kw):
        self.check()
        payload = {"cohort": self.cohort, "action": action, **kw}
        r = subprocess.run(
            self.command(host),
            input=json.dumps(payload).encode() + b"\n",
            capture_output=True,
            check=False,
            timeout=200,
        )
        if r.returncode:
            self.record(
                action + "-failure-" + str(time.time_ns()) + ".json",
                {
                    "host": host,
                    "action": action,
                    "exit": r.returncode,
                    "diagnostic": r.stderr[-1024:].decode(errors="replace"),
                },
            )
            raise RuntimeError("Fixed control failed: " + action)
        value = json.loads(r.stdout)
        self.record(action + "-" + str(time.time_ns()) + ".json", value)
        print(json.dumps({"action": action, **value}), flush=True)
        return value

    def stream(self, snapshot):
        self.check()
        s = subprocess.Popen(
            self.command("188.245.247.156"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        t = subprocess.Popen(
            self.command("2.31.22.33"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            s.stdin.write(
                json.dumps({"cohort": self.cohort, "action": "stream"}).encode() + b"\n"
            )
            s.stdin.close()
            t.stdin.write(
                json.dumps(
                    {"cohort": self.cohort, "action": "import", "snapshot": snapshot}
                ).encode()
                + b"\n"
            )
            start = time.monotonic()
            size = 0
            h = hashlib.sha256()
            while block := s.stdout.read(256 * 1024):
                self.check()
                if time.monotonic() - start > 180:
                    raise TimeoutError("Transfer bound")
                size += len(block)
                assert size <= snapshot["bytes"]
                h.update(block)
                t.stdin.write(block)
                delay = size / (4 * 1024**2) - (time.monotonic() - start)
                if delay > 0:
                    time.sleep(min(delay, 0.1))
            t.stdin.close()
            assert s.wait(timeout=10) == 0
            output = t.stdout.read(16385)
            assert t.wait(timeout=45) == 0 and len(output) <= 16384
            assert size == snapshot["bytes"] and h.hexdigest() == snapshot["sha256"]
            value = json.loads(output)
            assert value == {"restored": True, "sha256": snapshot["sha256"]}
            self.record(
                "transferred.json", value | {"bytes": size, "cohort": self.cohort}
            )
            print(
                json.dumps(
                    {"action": "transfer", "bytes": size, "sha256": h.hexdigest()}
                ),
                flush=True,
            )
        finally:
            for process in (s, t):
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
                for name in ("stdin", "stdout", "stderr"):
                    f = getattr(process, name)
                    if f is not None and not f.closed:
                        f.close()

    def run(self, publish):
        source = self.call("188.245.247.156", "prepare")
        self.call("2.31.22.33", "prepare", source=source)
        self.call("188.245.247.156", "arm")
        self.armed = True
        try:
            self.call("188.245.247.156", "pause")
            snapshot = self.call("188.245.247.156", "snapshot")
            self.stream(snapshot)
            self.call("2.31.22.33", "start")
            self.call("188.245.247.156", "proxy", mode="fenced")
            self.call("188.245.247.156", "proxy", mode="original")
            self.record(
                "proxy-rehearsed.json",
                {
                    "actual_reload_rollback": True,
                    "ingestion_unchanged": True,
                    "public_writes_fenced": True,
                },
            )
            if not publish:
                self.call("188.245.247.156", "rollback")
                return
            self.call("188.245.247.156", "proxy", mode="complete")
            self.call("2.31.22.33", "accept")
            self.call("188.245.247.156", "accept", target_accepted=True)
            self.record(
                "accepted.json",
                {
                    "cohort": self.cohort,
                    "snapshot": snapshot,
                    "deletion_enabled": False,
                },
            )
        except BaseException:
            # Watchdog independently performs the same fence-first order if lost.
            if self.armed:
                try:
                    self.call("188.245.247.156", "rollback")
                except Exception as error:  # noqa: BLE001 - watchdog remains armed
                    self.record(
                        "rollback-unconfirmed.json",
                        {"error_type": type(error).__name__, "watchdog_armed": True},
                    )
            raise


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--known-hosts", type=Path, required=True)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--publish", action="store_true")
    a = p.parse_args()
    Coordinator(a.known_hosts.resolve(), a.state.resolve()).run(a.publish)
