"""Existing Archive Root metadata path: return only the new schema request."""

import re
import hashlib
import json
import os
import time
from pathlib import Path

from _schema_wire import digest, need, private, read_json, write_json


def fetch_main():
    deadline = time.monotonic() + 12
    try:
        need(
            os.geteuid() == 0
            and hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest()
            == "b09421f9d8d4e1c392da9b1cca2ccb2e5d710ae73cf7051a615c3dc040bbd0a9",
            "fetch_archive_machine",
        )
        write_json(1, {"ready_for_control": True}, deadline)
        spec = read_json(0, deadline)
        folder = private(spec["directory"], directory=True)
        need(
            str(folder).startswith("/var/lib/greenmind/")
            and not (folder / "response.json").exists(),
            "fetch_new_own_scope",
        )
        path = folder / "request.json"
        while not path.exists():
            need(time.monotonic() < deadline, "fetch_request_not_ready")
            time.sleep(0.1)
        private(path)
        need(path.stat().st_size <= 32768, "fetch_request_bound")
        request = json.loads(path.read_bytes())
        need(
            set(request)
            == {
                "format",
                "scope_sha256",
                "source_identity_sha256",
                "source_machine_sha256",
                "source_container",
                "database_oid",
                "server_version_num",
                "snapshot",
                "program_sha256",
                "transport_binding_sha256",
                "pg_dump_args",
                "max_bytes",
                "application_data_authorized",
            }
            and request["format"] == "greenmind.private-source-schema-request.v1"
            and request["application_data_authorized"] is False
            and request["database_oid"] == 16384
            and request["scope_sha256"] == spec["scope_sha256"]
            and request["program_sha256"] == spec["program_sha256"]
            and request["transport_binding_sha256"] == spec["transport_sha256"]
            and re.fullmatch(
                r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{8}-[0-9A-Fa-f]{1,16}", request["snapshot"]
            ),
            "fetch_exact_request",
        )
        write_json(1, {"request": request, "request_sha256": digest(request)}, deadline)
        return 0
    except BaseException:
        try:
            write_json(
                1, {"failed": True, "code": "private_request_metadata_failed"}, time.monotonic() + 1
            )
        except BaseException:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(fetch_main())
