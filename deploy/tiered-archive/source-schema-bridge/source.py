"""Main Root: exactly one bounded schema-only PostgreSQL Docker child."""

import hashlib
import http.client
import json
import os
import re
import select
import stat
import struct
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from _schema_wire import (
    MAX_BYTES,
    BLOCK,
    Closed,
    digest,
    need,
    read_json,
    startup,
    write_all,
    write_json,
)

MACHINE = "065c65fd7f52297f59a00eddc22986c3b0c1530aa7e12b99e9d4d88c1155dd58"
CID = "fa5679cd84c16b5024251bb1f93414201fb091900af590cb63a7ecc087dddb71"
IMAGE = "sha256:f74040a77544227fc269b66ed23d9e0205d53f706c8ddaf00ed7032440b271ca"


def headroom():
    memory = next(
        int(line.split()[1])
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    need(memory >= 800 * 1024 and os.getloadavg()[0] <= 2.0, "source_headroom")


def container():
    child = subprocess.run(
        [
            "/usr/bin/docker",
            "inspect",
            "--format",
            '{"id":{{json .Id}},"image":{{json .Image}},"pid":{{json .State.Pid}},"started":{{json .State.StartedAt}},"running":{{json .State.Running}}}',
            CID,
        ],
        capture_output=True,
        timeout=4,
        check=False,
    )
    need(child.returncode == 0 and len(child.stdout) <= 16384, "source_container_metadata")
    result = json.loads(child.stdout)
    need(
        result["id"] == CID
        and result["image"] == IMAGE
        and result["running"] is True
        and type(result["pid"]) is int
        and result["pid"] > 1,
        "source_container_identity",
    )
    result["ticks"] = int(
        (Path("/proc") / str(result["pid"]) / "stat").read_text().rsplit(")", 1)[1].split()[19]
    )
    return result


def validate(spec):
    need(
        set(spec)
        == {
            "request",
            "scope_sha256",
            "source_identity_sha256",
            "source_postmaster_start",
            "transport_sha256",
            "program_sha256",
            "source_unit",
        },
        "source_spec",
    )
    request = spec["request"]
    snapshot = request["snapshot"]
    need(request.get("application_data_authorized") is False, "schema_only_not_master")
    need(
        isinstance(snapshot, str)
        and re.fullmatch(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{8}-[0-9A-Fa-f]{1,16}", snapshot),
        "source_snapshot",
    )
    need(
        request
        == {
            "format": "greenmind.private-source-schema-request.v1",
            "scope_sha256": spec["scope_sha256"],
            "source_identity_sha256": spec["source_identity_sha256"],
            "source_machine_sha256": MACHINE,
            "source_container": CID,
            "database_oid": 16384,
            "server_version_num": 150010,
            "snapshot": snapshot,
            "program_sha256": spec["program_sha256"],
            "transport_binding_sha256": spec["transport_sha256"],
            "pg_dump_args": [
                "--no-password",
                "--format=custom",
                "--schema-only",
                "--no-owner",
                "--no-acl",
                "--no-blobs",
                "--snapshot=" + snapshot,
                "--dbname=plantdb",
            ],
            "max_bytes": MAX_BYTES,
            "application_data_authorized": False,
        },
        "schema_only_request",
    )
    need(
        all(
            isinstance(spec[key], str) and re.fullmatch(r"[a-f0-9]{64}", spec[key])
            for key in (
                "scope_sha256",
                "source_identity_sha256",
                "transport_sha256",
                "program_sha256",
            )
        ),
        "source_pins",
    )
    need(
        datetime.fromisoformat(spec["source_postmaster_start"]).tzinfo is not None,
        "source_postmaster_timezone",
    )
    return request


def source_main():
    deadline = time.monotonic() + 65
    child = None
    try:
        unit = sys.argv[1]
        own = startup(unit)
        need(
            hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest() == MACHINE,
            "source_machine",
        )
        write_json(1, {"ready_for_control": True, "startup": own}, deadline)
        spec = read_json(0, deadline)
        need(spec["source_unit"] == unit, "source_unit_binding")
        request = validate(spec)
        headroom()
        health = Path("/etc/greenmind/receiver-health/gateway.json")
        ports = [8120]
        if health.exists():
            info = health.lstat()
            need(
                stat.S_ISREG(info.st_mode)
                and info.st_uid == 0
                and not info.st_mode & 0o022
                and info.st_size <= 1024,
                "source_health_config",
            )
            value = json.loads(health.read_bytes())
            need(
                value in ({"schema": 1, "ports": [8120]}, {"schema": 1, "ports": [8120, 8152]}),
                "source_health_routes",
            )
            ports = value["ports"]
        for port in [*ports, 8003, 8000]:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            try:
                connection.request("GET", "/health")
                need(connection.getresponse().status == 200, "source_health")
            finally:
                connection.close()
        before = container()
        sql = "SELECT json_build_object('database',current_database(),'oid',(SELECT oid::bigint FROM pg_database WHERE datname=current_database()),'postmaster_start',pg_postmaster_start_time(),'version',current_setting('server_version_num')::int,'readonly',current_setting('transaction_read_only'),'principal',current_user)"
        options = "-c default_transaction_read_only=on -c statement_timeout=8000 -c lock_timeout=150 -c work_mem=2048 -c TimeZone=UTC"
        query = subprocess.run(
            [
                "/usr/bin/docker",
                "exec",
                "--env",
                "PGOPTIONS=" + options,
                CID,
                "/bin/sh",
                "-c",
                'exec psql -X -qAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d plantdb -c "$1"',
                "fixed-schema-identity",
                sql,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        need(query.returncode == 0 and len(query.stdout) <= 16384, "source_physical_query")
        identity = json.loads(query.stdout)
        need(
            identity["database"] == "plantdb"
            and identity["oid"] == 16384
            and identity["version"] == 150010
            and identity["readonly"] == "on"
            and datetime.fromisoformat(identity["postmaster_start"]).astimezone(UTC)
            == datetime.fromisoformat(spec["source_postmaster_start"]).astimezone(UTC),
            "source_physical_identity",
        )
        headroom()
        write_json(
            1,
            {
                "kind": "schema-stream-v1",
                "request_sha256": digest(request),
                "startup": own,
                "source_generation_sha256": digest(before),
                "physical_identity_verified": True,
            },
            deadline,
        )
        remaining = min(45, int(deadline - time.monotonic()) - 5)
        need(remaining >= 1, "source_client_start_budget")
        # This Docker child is outside the host helper cgroup. Bound its OWN
        # address space and elapsed time as well; never change the DB container.
        command = 'case "$(command -v timeout)" in /bin/timeout|/usr/bin/timeout) ;; *) exit 71;; esac; ulimit -v 131072 || exit 72; exec nice -n 19 timeout -s TERM -k 3 "$GM_SCHEMA_SECONDS" pg_dump --no-password --format=custom --schema-only --no-owner --no-acl --no-blobs --snapshot="$GM_SCHEMA_SNAPSHOT" --dbname=plantdb -U "$POSTGRES_USER"'
        child = subprocess.Popen(
            [
                "/usr/bin/docker",
                "exec",
                "--env",
                "PGOPTIONS=" + options,
                "--env",
                "PGAPPNAME=" + unit.removesuffix(".service"),
                "--env",
                "GM_SCHEMA_SECONDS=" + str(remaining),
                "--env",
                "GM_SCHEMA_SNAPSHOT=" + request["snapshot"],
                CID,
                "/bin/sh",
                "-c",
                command,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        total, checksum, prefix = 0, hashlib.sha256(), bytearray()
        while True:
            need(time.monotonic() < deadline, "source_dump_deadline")
            headroom()
            if not select.select([child.stdout], [], [], 0.2)[0]:
                continue
            block = os.read(child.stdout.fileno(), BLOCK)
            if not block:
                break
            prefix.extend(block[: max(0, 5 - len(prefix))])
            need(len(prefix) < 5 or prefix == b"PGDMP", "source_custom_archive")
            total += len(block)
            need(total <= MAX_BYTES, "source_byte_budget")
            checksum.update(block)
            write_all(1, struct.pack(">I", len(block)) + block, deadline)
        result = child.wait(timeout=max(0.1, deadline - time.monotonic()))
        need(result == 0 and total >= 5, "source_dump_failed")
        need(container() == before, "source_changed_during_dump")
        headroom()
        write_all(1, struct.pack(">I", 0), deadline)
        write_json(
            1,
            {
                "kind": "schema-end-v1",
                "request_sha256": digest(request),
                "bytes": total,
                "sha256": checksum.hexdigest(),
                "owned_dump_wait_exit": result,
                "source_unchanged": True,
            },
            deadline,
        )
        return 0
    except BaseException as error:
        # Never print a Docker/SQL/SSH exception or a captured source payload.
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        try:
            write_json(
                1,
                {
                    "kind": "failed",
                    "code": str(error) if isinstance(error, Closed) else "source_operation_failed",
                    "source_pg_outcome": "unknown_after_error",
                    "publication_blocked": True,
                    "no_automatic_retry": True,
                },
                time.monotonic() + 1,
            )
        except BaseException:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(source_main())
