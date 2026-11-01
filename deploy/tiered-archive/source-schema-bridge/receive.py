"""Archive Root: private bounded stream sink and exact SourceSchemaCapture receipt."""

import hashlib
import json
import os
import struct
import sys
import time
from pathlib import Path

from _schema_wire import (
    BLOCK,
    MAX_BYTES,
    Closed,
    digest,
    need,
    private,
    read_exact,
    read_json,
    startup,
    write_json,
)

MACHINE = "b09421f9d8d4e1c392da9b1cca2ccb2e5d710ae73cf7051a615c3dc040bbd0a9"


def admit(folder, request):
    private(folder, directory=True)
    need(set(folder.iterdir()) == {folder / "request.json"}, "new_private_response")
    raw = (folder / "request.json").read_bytes()
    need(len(raw) <= 32768 and json.loads(raw) == request, "receiver_request_file")


def receive(fd, folder, request, deadline):
    admit(folder, request)
    header = read_json(fd, deadline)
    need(
        header.get("kind") == "schema-stream-v1"
        and header.get("request_sha256") == digest(request)
        and header.get("physical_identity_verified") is True,
        "receiver_source_header",
    )
    pending = folder / "schema.dump.pending"
    out = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    total, checksum, prefix = 0, hashlib.sha256(), bytearray()
    with os.fdopen(out, "wb") as stream:
        while True:
            size = struct.unpack(">I", read_exact(fd, 4, deadline))[0]
            if not size:
                break
            need(size <= BLOCK and total + size <= MAX_BYTES, "receiver_byte_budget")
            block = read_exact(fd, size, deadline)
            prefix.extend(block[: max(0, 5 - len(prefix))])
            need(len(prefix) < 5 or prefix == b"PGDMP", "receiver_custom_archive")
            total += size
            checksum.update(block)
            stream.write(block)
        stream.flush()
        os.fsync(stream.fileno())
    trailer = read_json(fd, deadline)
    need(
        trailer
        == {
            "kind": "schema-end-v1",
            "request_sha256": digest(request),
            "bytes": total,
            "sha256": checksum.hexdigest(),
            "owned_dump_wait_exit": 0,
            "source_unchanged": True,
        }
        and total >= 5,
        "receiver_source_trailer",
    )
    terminal = read_json(fd, deadline)
    need(
        terminal
        == {
            "kind": "source-admin-terminal-v1",
            "request_sha256": digest(request),
            "actual_owned_wait_exit": 0,
            "source_unit_terminal_verified": True,
        },
        "receiver_source_admin_terminal",
    )
    need(not (folder / "schema.dump").exists(), "receiver_no_overwrite")
    os.link(pending, folder / "schema.dump")
    # Both own copies remain private evidence; remove only the new pending link.
    pending.unlink()
    receipt = {
        "format": "greenmind.private-source-schema-response.v1",
        "request_sha256": digest(request),
        "owned_wait_exit": 0,
        "source_unchanged": True,
        "schema": {"name": "schema.dump", "bytes": total, "sha256": checksum.hexdigest()},
    }
    target = folder / "response.ready.json"
    out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(out, "wb") as stream:
        stream.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode())
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {
        "bytes": total,
        "sha256": checksum.hexdigest(),
        "receipt_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }


def receive_main():
    deadline = time.monotonic() + 85
    try:
        own = startup(sys.argv[1])
        need(
            hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest() == MACHINE,
            "receiver_machine",
        )
        available = next(
            int(line.split()[1])
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")
        )
        need(available >= 1024 * 1024 and os.getloadavg()[0] <= 2.0, "receiver_headroom")
        write_json(1, {"ready_for_control": True, "startup": own}, deadline)
        spec = read_json(0, deadline)
        need(
            set(spec) == {"directory", "request", "receiver_unit"}
            and spec["receiver_unit"] == sys.argv[1],
            "receiver_spec",
        )
        folder = private(spec["directory"], directory=True)
        need(str(folder).startswith("/var/lib/greenmind/"), "receiver_root_scope")
        admit(folder, spec["request"])
        write_json(
            1, {"receiver_admitted": True, "request_sha256": digest(spec["request"])}, deadline
        )
        result = receive(0, folder, spec["request"], deadline)
        write_json(1, {"passed": True, **result, "startup": own}, deadline)
        return 0
    except BaseException as error:
        try:
            write_json(
                1,
                {
                    "passed": False,
                    "code": str(error)
                    if isinstance(error, Closed)
                    else "receiver_operation_failed",
                    "no_automatic_retry": True,
                },
                time.monotonic() + 1,
            )
        except BaseException:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(receive_main())
