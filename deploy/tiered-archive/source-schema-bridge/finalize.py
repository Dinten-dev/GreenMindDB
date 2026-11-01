"""Only own terminated units; Archive promotes only its new exact response."""

import re
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from _schema_wire import Closed, need, private, read_json, write_json


def finalize_main():
    deadline = time.monotonic() + 10
    try:
        write_json(1, {"ready_for_control": True}, deadline)
        spec = read_json(0, deadline)
        role, unit, own = spec["role"], spec["unit"], spec["startup"]
        expected = {
            "source": "065c65fd7f52297f59a00eddc22986c3b0c1530aa7e12b99e9d4d88c1155dd58",
            "archive": "b09421f9d8d4e1c392da9b1cca2ccb2e5d710ae73cf7051a615c3dc040bbd0a9",
        }
        need(
            os.geteuid() == 0
            and role in expected
            and hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest() == expected[role],
            "finalize_machine",
        )
        need(
            re.fullmatch(r"gm-schema-(source|receive)-[a-f0-9]{32}\.service", unit)
            and own["cgroup"] == "/sys/fs/cgroup/system.slice/" + unit,
            "own_terminal_scope",
        )
        while True:
            process = Path("/proc") / str(own["pid"]) / "stat"
            gone = (
                not process.exists()
                or int(process.read_text().rsplit(")", 1)[1].split()[19]) != own["ticks"]
            )
            call = subprocess.run(
                [
                    "/usr/bin/systemctl",
                    "show",
                    unit,
                    "-p",
                    "LoadState",
                    "-p",
                    "ActiveState",
                    "-p",
                    "MainPID",
                    "-p",
                    "InvocationID",
                    "-p",
                    "Job",
                ],
                capture_output=True,
                timeout=2,
                check=False,
            )
            need(call.returncode in {0, 1, 4} and len(call.stdout) <= 8192, "own_manager_terminal")
            manager = dict(line.split("=", 1) for line in call.stdout.decode().splitlines())
            if (
                gone
                and not Path(own["cgroup"]).exists()
                and manager
                == {
                    "LoadState": "not-found",
                    "ActiveState": "inactive",
                    "MainPID": "0",
                    "InvocationID": "",
                    "Job": "",
                }
            ):
                break
            need(time.monotonic() < deadline, "own_terminal_unknown")
            time.sleep(0.2)
        result = {"terminal": True, "own_generation_gone": True, "own_cgroup_absent": True}
        if role == "archive":
            folder = private(spec["directory"], directory=True)
            ready = private(folder / "response.ready.json")
            need(ready.stat().st_size <= 32768, "own_receipt_bound")
            raw = ready.read_bytes()
            need(
                hashlib.sha256(raw).hexdigest() == spec["receipt_sha256"]
                and json.loads(raw)["request_sha256"] == spec["request_sha256"]
                and not (folder / "response.json").exists(),
                "own_receipt_binding",
            )
            # Unique Root-private run has only this writer; never replace a prior response.
            os.rename(ready, folder / "response.json")
            fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            result["receipt_promoted"] = True
        write_json(1, result, deadline)
        return 0
    except BaseException as error:
        try:
            write_json(
                1,
                {
                    "terminal": False,
                    "code": str(error) if isinstance(error, Closed) else "terminal_metadata_failed",
                },
                time.monotonic() + 1,
            )
        except BaseException:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(finalize_main())
