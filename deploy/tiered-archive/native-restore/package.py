"""Package a manual bounded Staging acceptance probe; never execute remotely."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path


def package(source, output):
    if source not in {"gateway", "direct"}:
        raise ValueError("Only existing Staging sources are allowed")
    root = Path(__file__).resolve().parent
    worker = (root / "worker.py").read_bytes()
    supervisor = (root / "supervisor.template.py").read_text()
    if (
        supervisor.count("PAYLOAD = None") != 1
        or supervisor.count("SOURCE = None") != 1
    ):
        raise ValueError("Unexpected supervisor template")
    payload = {
        "data": base64.b64encode(worker).decode("ascii"),
        "sha256": hashlib.sha256(worker).hexdigest(),
    }
    prepared = supervisor.replace(
        "PAYLOAD = None", "PAYLOAD = " + repr(payload)
    ).replace("SOURCE = None", "SOURCE = " + repr(source))
    compile(worker, "worker.py", "exec")
    compile(prepared, "supervisor.py", "exec")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as target:
        target.write(prepared)
        target.flush()
        os.fsync(target.fileno())
    return {
        "source": source,
        "worker_sha256": payload["sha256"],
        "package_sha256": hashlib.sha256(prepared.encode()).hexdigest(),
        "remote_execution": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=("gateway", "direct"))
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(package(args.source, args.output)))
