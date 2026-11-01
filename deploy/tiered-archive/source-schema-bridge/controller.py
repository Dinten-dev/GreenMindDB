"""Explicit one-off private bridge using the existing Mac OpenSSH admin credentials.

Default is inert. Never writes schema bytes to Mac disk, stdout or logs.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import stat
import struct
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADMIN = Path(
    "/Users/traver.dinten@fhnw.ch/Documents/Codex/2026-10-12/halllo-traver-ich-habe-heute-experimentiert/work/admin_probe.py"
)
ADMIN_SHA = "23e84270bf85c9124df235d5cce3259dc5b2a34239145d3a4b9c6759dc06a94b"
KNOWN = "/Users/traver.dinten@fhnw.ch/.codex/.chatgpt-projects/g-p-699ef16e0d1c81919fe50053578520a6/implementation/validation/archive-server-rollout-20261008/known_hosts"
HOSTS = {"source": "188.245.247.156", "archive": "2.31.22.33"}
spec = importlib.util.spec_from_file_location("private_schema_wire", HERE / "wire.py")
wire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire)


def program(name):
    prefix = (
        "import sys,types\n_schema=types.ModuleType('_schema_wire')\nexec(compile("
        + repr((HERE / "wire.py").read_text())
        + ",'pinned_private_schema_wire','exec'),_schema.__dict__)\nsys.modules['_schema_wire']=_schema\n"
    )
    return prefix.encode() + (HERE / name).read_bytes()


def binding():
    return wire.digest(
        {
            name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
            for name in (
                "controller.py",
                "wire.py",
                "source.py",
                "receive.py",
                "finalize.py",
                "fetch.py",
            )
        }
    )


def validate_scope(scope):
    wire.need(
        isinstance(scope, dict)
        and set(scope)
        == {
            "format",
            "execution_authorized",
            "expires_unix",
            "run_id",
            "directory",
            "provider_scope_sha256",
            "source_identity_sha256",
            "source_postmaster_start",
            "transport_binding_sha256",
        },
        "exact_operator_scope",
    )
    wire.need(
        scope["format"] == "greenmind.private-admin-schema-bridge.v1"
        and scope["execution_authorized"] is True
        and type(scope["expires_unix"]) is int
        and time.time() < scope["expires_unix"] <= time.time() + 300
        and re.fullmatch(r"[a-f0-9]{32}", scope["run_id"]),
        "fresh_oneoff_operator_scope",
    )
    wire.need(
        all(
            isinstance(scope[name], str) and re.fullmatch(r"[a-f0-9]{64}", scope[name])
            for name in (
                "provider_scope_sha256",
                "source_identity_sha256",
                "transport_binding_sha256",
            )
        )
        and scope["transport_binding_sha256"] == binding(),
        "operator_immutable_bindings",
    )
    wire.need(
        re.fullmatch(r"/var/lib/greenmind/[a-zA-Z0-9_./-]+", scope["directory"])
        and ".." not in Path(scope["directory"]).parts,
        "operator_private_root",
    )
    return scope


class ExistingAdmin:
    """Exactly admin_probe's existing SSH/sudo identities; no key discovery."""

    def password(self, role):
        wire.need(
            hashlib.sha256(ADMIN.read_bytes()).hexdigest() == ADMIN_SHA,
            "existing_admin_program_changed",
        )
        path = ADMIN.parent / "private" / (role + "-sudo.txt")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            wire.need(
                stat.S_ISREG(before.st_mode)
                and before.st_uid == os.getuid()
                and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) == 0o600
                and before.st_size <= 1024,
                "existing_private_admin_credential",
            )
            raw = stream.read(1025)
            after = os.fstat(stream.fileno())
        signature = lambda item: (
            item.st_dev,
            item.st_ino,
            item.st_size,
            item.st_mtime_ns,
            item.st_ctime_ns,
        )
        wire.need(
            signature(before) == signature(after) == signature(path.lstat()),
            "admin_credential_changed",
        )
        raw = raw.rstrip(b"\r\n")
        wire.need(raw and b"\n" not in raw and b"\0" not in raw, "admin_credential_shape")
        return raw

    def start(self, role, code, *, unit=None, directory=None):
        password = self.password(role)
        args = [
            "/usr/bin/ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "StrictHostKeyChecking=yes",
        ]
        if role == "archive":
            args += ["-o", "UserKnownHostsFile=" + KNOWN]
        args += ["traver@" + HOSTS[role]]
        remote = ["sudo", "-S", "-p", "", "--"]
        if unit:
            wire.need(
                re.fullmatch(r"gm-schema-(source|receive)-[a-f0-9]{32}\.service", unit),
                "own_bridge_unit",
            )
            remote += [
                "/usr/bin/systemd-run",
                "--quiet",
                "--pipe",
                "--wait",
                "--collect",
                "--unit=" + unit,
            ]
            properties = [
                "MemoryMax=64M",
                "MemoryHigh=56M",
                "MemorySwapMax=0",
                "CPUQuota=5%",
                "TasksMax=32",
                "TimeoutStopSec=5",
                "KillMode=control-group",
                "Type=oneshot",
                "UMask=0077",
                "NoNewPrivileges=true",
                "PrivateTmp=true",
                "ProtectSystem=strict",
                "ProtectHome=true",
            ]
            properties += [
                "RuntimeMaxSec=" + ("75" if role == "source" else "105"),
                "TimeoutStartSec=" + ("75" if role == "source" else "105"),
            ]
            if role == "archive":
                wire.need(
                    directory
                    and re.fullmatch(r"/var/lib/greenmind/[a-zA-Z0-9_./-]+", directory)
                    and ".." not in Path(directory).parts,
                    "own_bridge_directory",
                )
                properties += ["ReadWritePaths=" + directory]
            remote += ["--property=" + item for item in properties]
        remote += ["/usr/bin/python3", "-c", code.decode()]
        if unit:
            remote += [unit]
        child = subprocess.Popen(
            [*args, shlex.join(remote)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        # Only sudo receives this existing host's password. No argv/environment/file copy.
        wire.write_all(child.stdin.fileno(), password + b"\n", time.monotonic() + 8)
        return child


def wait0(child, deadline):
    child.stdin.close()
    wire.need(
        child.wait(timeout=max(0.1, deadline - time.monotonic())) == 0, "owned_admin_exit_failed"
    )


def root_control(admin, role, value, deadline):
    child = admin.start(role, program("finalize.py"))
    try:
        wire.need(
            wire.read_json(child.stdout.fileno(), deadline).get("ready_for_control") is True,
            "terminal_control_ready",
        )
        wire.write_json(child.stdin.fileno(), value, deadline)
        result = wire.read_json(child.stdout.fileno(), deadline)
        wait0(child, deadline)
        wire.need(result.get("terminal") is True, "own_root_terminal_unknown")
        return result
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def relay_body(source, receiver, request, deadline, *, expected_startup=None):
    """Actual bounded pipe relay, also used by local real-pipe regression tests."""
    header = wire.read_json(source, deadline)
    wire.need(
        header.get("kind") == "schema-stream-v1"
        and header.get("request_sha256") == wire.digest(request)
        and header.get("physical_identity_verified") is True,
        "source_stream_header",
    )
    wire.need(
        expected_startup is None or header.get("startup") == expected_startup,
        "source_startup_binding",
    )
    wire.write_json(receiver, header, deadline)
    total, checksum = 0, hashlib.sha256()
    while True:
        raw = wire.read_exact(source, 4, deadline)
        size = struct.unpack(">I", raw)[0]
        wire.need(size <= wire.BLOCK and total + size <= wire.MAX_BYTES, "bridge_byte_budget")
        wire.write_all(receiver, raw, deadline)
        if not size:
            break
        block = wire.read_exact(source, size, deadline)
        total += size
        checksum.update(block)
        wire.write_all(receiver, block, deadline)
    trailer = wire.read_json(source, deadline)
    wire.need(
        trailer
        == {
            "kind": "schema-end-v1",
            "request_sha256": wire.digest(request),
            "bytes": total,
            "sha256": checksum.hexdigest(),
            "owned_dump_wait_exit": 0,
            "source_unchanged": True,
        },
        "source_stream_trailer",
    )
    wire.write_json(receiver, trailer, deadline)
    return header, {"bytes": total, "sha256": checksum.hexdigest()}


def fetch_request(admin, scope, deadline):
    validate_scope(scope)
    child = admin.start("archive", program("fetch.py"))
    try:
        wire.need(
            wire.read_json(child.stdout.fileno(), deadline).get("ready_for_control") is True,
            "fetch_ready",
        )
        wire.write_json(
            child.stdin.fileno(),
            {
                "directory": scope["directory"],
                "scope_sha256": scope["provider_scope_sha256"],
                "program_sha256": hashlib.sha256(program("source.py")).hexdigest(),
                "transport_sha256": binding(),
            },
            deadline,
        )
        result = wire.read_json(child.stdout.fileno(), deadline)
        wait0(child, deadline)
        wire.need(
            "request" in result and result["request_sha256"] == wire.digest(result["request"]),
            "fetch_response",
        )
        return result["request"]
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def bridge(admin, scope, request, *, record=lambda phase, value: None):
    validate_scope(scope)
    deadline = time.monotonic() + 100
    source_unit = "gm-schema-source-" + scope["run_id"] + ".service"
    receiver_unit = "gm-schema-receive-" + scope["run_id"] + ".service"
    wire.need(
        request["program_sha256"] == hashlib.sha256(program("source.py")).hexdigest()
        and request["transport_binding_sha256"] == binding()
        and request["scope_sha256"] == scope["provider_scope_sha256"]
        and request["source_identity_sha256"] == scope["source_identity_sha256"],
        "reviewed_bridge_bindings",
    )
    source = receiver = None
    try:
        receiver = admin.start(
            "archive", program("receive.py"), unit=receiver_unit, directory=scope["directory"]
        )
        record("receiver_handle", {"owned_PID": receiver.pid, "unit": receiver_unit})
        receiver_ready = wire.read_json(receiver.stdout.fileno(), deadline)
        record("receiver_startup", receiver_ready)
        wire.need(receiver_ready.get("ready_for_control") is True, "receiver_ready")
        wire.write_json(
            receiver.stdin.fileno(),
            {"directory": scope["directory"], "request": request, "receiver_unit": receiver_unit},
            deadline,
        )
        wire.need(
            wire.read_json(receiver.stdout.fileno(), deadline)
            == {"receiver_admitted": True, "request_sha256": wire.digest(request)},
            "receiver_not_admitted",
        )
        source = admin.start("source", program("source.py"), unit=source_unit)
        record(
            "source_handle",
            {
                "owned_PID": source.pid,
                "unit": source_unit,
                "own_pg_application_name": source_unit.removesuffix(".service"),
            },
        )
        source_ready = wire.read_json(source.stdout.fileno(), deadline)
        record("source_startup", source_ready)
        wire.need(source_ready.get("ready_for_control") is True, "source_ready")
        wire.write_json(
            source.stdin.fileno(),
            {
                "request": request,
                "scope_sha256": scope["provider_scope_sha256"],
                "source_identity_sha256": scope["source_identity_sha256"],
                "source_postmaster_start": scope["source_postmaster_start"],
                "transport_sha256": binding(),
                "program_sha256": request["program_sha256"],
                "source_unit": source_unit,
            },
            deadline,
        )
        header, proof = relay_body(
            source.stdout.fileno(),
            receiver.stdin.fileno(),
            request,
            deadline,
            expected_startup=source_ready["startup"],
        )
        wire.need(header["startup"] == source_ready["startup"], "source_startup_binding")
        wait0(source, deadline)
        terminal = root_control(
            admin,
            "source",
            {"role": "source", "unit": source_unit, "startup": source_ready["startup"]},
            deadline,
        )
        record("source_terminal", {"actual_owned_wait_exit": source.returncode, **terminal})
        wire.write_json(
            receiver.stdin.fileno(),
            {
                "kind": "source-admin-terminal-v1",
                "request_sha256": wire.digest(request),
                "actual_owned_wait_exit": 0,
                "source_unit_terminal_verified": True,
            },
            deadline,
        )
        result = wire.read_json(receiver.stdout.fileno(), deadline)
        wait0(receiver, deadline)
        wire.need(
            result.get("passed") is True
            and result.get("sha256") == proof["sha256"]
            and result.get("bytes") == proof["bytes"]
            and result["startup"] == receiver_ready["startup"],
            "receiver_independent_proof",
        )
        terminal = root_control(
            admin,
            "archive",
            {
                "role": "archive",
                "unit": receiver_unit,
                "startup": receiver_ready["startup"],
                "directory": scope["directory"],
                "receipt_sha256": result["receipt_sha256"],
                "request_sha256": wire.digest(request),
            },
            deadline,
        )
        record("receiver_terminal", {"actual_owned_wait_exit": receiver.returncode, **terminal})
        return {
            "passed": True,
            **proof,
            "source_admin_owned_PID": source.pid,
            "receiver_admin_owned_PID": receiver.pid,
            "source_owned_wait_exit": 0,
            "receiver_owned_wait_exit": 0,
            "source_and_receiver_units_terminal": True,
            "no_mac_plaintext_file": True,
        }
    finally:
        # Only these owned local SSH clients. Server one-shot units retain their
        # finite hard deadlines; failure is unknown, never an automatic retry.
        for child in (source, receiver):
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scope", type=Path, nargs="?")
    parser.add_argument("request", type=Path, nargs="?")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"inert": True, "native_calls": 0}))
        return 0
    try:
        scope = json.loads(args.scope.read_bytes())
        validate_scope(scope)

        # Request contains only snapshot/Scope metadata, copied by the existing
        # Root operator's metadata channel; never schema bytes or credentials.
        def record(phase, value):
            path = args.scope.parent / ("Bridge-" + phase + ".json")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as body:
                json.dump(value, body, sort_keys=True)
                body.flush()
                os.fsync(body.fileno())

        record(
            "intent",
            {
                "run_id": scope["run_id"],
                "scope_sha256": hashlib.sha256(args.scope.read_bytes()).hexdigest(),
                "at": time.time(),
                "no_automatic_retry": True,
            },
        )
        admin = ExistingAdmin()
        request = (
            json.loads(args.request.read_bytes())
            if args.request
            else fetch_request(admin, scope, time.monotonic() + 20)
        )
        result = bridge(admin, scope, request, record=record)
        record("result", result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except BaseException:
        print(
            json.dumps(
                {
                    "passed": False,
                    "code": "private_schema_bridge_failed",
                    "source_pg_outcome": "unknown_after_error",
                    "publication_blocked": True,
                    "no_automatic_retry": True,
                    "raw_error_exported": False,
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
