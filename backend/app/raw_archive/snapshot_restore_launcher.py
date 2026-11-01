"""Root-owned isolated snapshot restore launcher; no app imports before audit.

Invoke as: python3 -I -B /root/private/launcher.py --scope FILE --sha256 SHA.
The same process writes its startup identity, waits for a root handoff, audits
again, restores through one of two fixed entrypoints and seals its inputs.
"""

import argparse
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path


def need(value):
    if not value:
        raise ValueError("preimport_snapshot_closed")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def root_file(path, expected, *, mode=0o400, limit=1024 * 1024):
    path = Path(path)
    need(
        path.is_absolute()
        and path.resolve(strict=False) == path
        and re.fullmatch(r"[a-f0-9]{64}", expected)
    )
    for parent in path.parents:
        info = parent.stat(follow_symlinks=False)
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as body:
        before = os.fstat(body.fileno())
        need(
            stat.S_ISREG(before.st_mode)
            and before.st_uid == 0
            and before.st_nlink == 1
            and stat.S_IMODE(before.st_mode) == mode
            and before.st_size <= limit
        )
        raw = body.read(limit + 1)
        after = os.fstat(body.fileno())
    need(
        len(raw) == before.st_size
        and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        and digest(raw) == expected
    )
    return raw


def create_root_file(path, data):
    path = Path(path)
    need(path.is_absolute() and path.resolve(strict=False) == path)
    for parent in path.parents:
        info = parent.stat(follow_symlinks=False)
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, "wb") as body:
        os.fchmod(body.fileno(), 0o400)
        body.write(raw)
        body.flush()
        os.fsync(body.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return digest(raw)


def read_root_handoff(path):
    path = Path(path)
    need(path.is_absolute() and path.resolve(strict=False) == path)
    for parent in path.parents:
        info = parent.stat(follow_symlinks=False)
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as body:
        before = os.fstat(body.fileno())
        need(
            stat.S_ISREG(before.st_mode)
            and before.st_uid == 0
            and before.st_nlink == 1
            and stat.S_IMODE(before.st_mode) == 0o400
            and before.st_size <= 16384
        )
        raw = body.read(16385)
        after = os.fstat(body.fileno())
    need(
        len(raw) == before.st_size
        and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    )
    return raw


def audit_runtime(root, manifest_sha, runtime_sha):
    root = Path(root)
    need(root.is_absolute() and root.resolve(strict=False) == root and not root.is_symlink())
    root_stat = root.lstat()
    need(
        stat.S_ISDIR(root_stat.st_mode)
        and root_stat.st_uid == 0
        and stat.S_IMODE(root_stat.st_mode) == 0o555
    )
    manifest_raw = root_file(root / "manifest.json", manifest_sha, mode=0o444, limit=262144)
    manifest = json.loads(manifest_raw)
    need(
        type(manifest) is dict
        and type(manifest.get("files")) is dict
        and type(manifest.get("commit")) is str
        and re.fullmatch(r"[a-f0-9]{40}", manifest["commit"])
        and manifest.get("runtime_sha256") == runtime_sha
        and 1 <= len(manifest["files"]) <= 100000
    )
    seen = set()
    visited = 0
    for parent, directories, files in os.walk(root, followlinks=False):
        visited += len(directories) + len(files)
        need(visited <= 200000 and len(seen) <= 100000)
        for name in directories:
            path = Path(parent) / name
            info = path.lstat()
            need(
                stat.S_ISDIR(info.st_mode)
                and info.st_uid == 0
                and stat.S_IMODE(info.st_mode) == 0o555
            )
        for name in files:
            path = Path(parent) / name
            relative = path.relative_to(root).as_posix()
            need(path.suffix != ".pyc")
            if relative == "manifest.json":
                continue
            need(relative in manifest["files"])
            root_file(path, manifest["files"][relative], mode=0o444, limit=8 * 1024**2)
            seen.add(relative)
    need(seen == set(manifest["files"]))
    backend = root / "backend"
    files = sorted(
        [
            *(backend / "app").rglob("*.py"),
            *(backend / "app").rglob("*.sql"),
            backend / "requirements.lock",
            backend / "requirements-archive.lock",
        ]
    )
    result = hashlib.sha256()
    for path in files:
        rel = path.relative_to(root).as_posix()
        need(rel in manifest["files"])
        result.update(path.relative_to(backend).as_posix().encode() + b"\0")
        result.update(root_file(path, manifest["files"][rel], mode=0o444, limit=8 * 1024**2))
    need(result.hexdigest() == runtime_sha)


def consumer_identity(unit, invocation):
    need(os.geteuid() == 0 and sys.flags.isolated and sys.dont_write_bytecode)
    need(type(unit) is str and re.fullmatch(r"[A-Za-z0-9_.@:-]+\.service", unit))
    need(
        type(invocation) is str
        and re.fullmatch(r"[a-f0-9]{32}", invocation)
        and os.environ.get("INVOCATION_ID") == invocation
    )
    raw = Path("/proc/self/stat").read_text()
    start = raw[raw.rindex(")") + 2 :].split()[19]
    need(start.isdecimal() and int(start) > 0)
    groups = [
        line[3:]
        for line in Path("/proc/self/cgroup").read_text().splitlines()
        if line.startswith("0::")
    ]
    need(groups == ["/system.slice/" + unit])
    status = subprocess.run(  # noqa: S603 -- fixed binary and validated unit argument
        [
            "/usr/bin/systemctl",
            "show",
            unit,
            "--property=Id,ActiveState,MainPID,InvocationID,ControlGroup",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    need(status.returncode == 0 and len(status.stdout) <= 8192)
    lines = status.stdout.decode("utf-8").splitlines()
    need(all("=" in line for line in lines))
    state = dict(line.split("=", 1) for line in lines)
    need(
        len(state) == len(lines)
        and set(state) == {"Id", "ActiveState", "MainPID", "InvocationID", "ControlGroup"}
        and state["Id"] == unit
        and state["ActiveState"] == "active"
        and state["MainPID"] == str(os.getpid())
        and state["InvocationID"] == invocation
        and state["ControlGroup"] == groups[0]
    )
    info = (Path("/sys/fs/cgroup") / groups[0].lstrip("/")).stat()
    startup = {
        "format": "greenmind.managed-snapshot.consumer-startup.v1",
        "pid": os.getpid(),
        "start_ticks": int(start),
        "unit": unit,
        "invocation": invocation,
        "cgroup": groups[0],
        "cgroup_device": info.st_dev,
        "cgroup_inode": info.st_ino,
    }
    owner = {
        "host": socket.gethostname(),
        "unit": unit,
        "invocation": invocation,
        "pid_generation": f"{os.getpid()}:{start}",
    }
    return startup, owner


def seal_restore_inputs(output, frozen, inventory, entrypoint):
    """Copy exactly the verified snapshot inputs to an immutable private directory."""
    import shutil

    output, frozen = Path(output), Path(frozen)
    need(output.is_absolute() and frozen.is_absolute() and output != frozen)
    need(not frozen.exists() and not frozen.is_symlink())
    for parent in frozen.parents:
        info = parent.stat(follow_symlinks=False)
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    need(
        type(inventory) is dict
        and set(inventory) == {"schema", "files"}
        and inventory["schema"] == 1
        and type(inventory["files"]) is list
        and 1 <= len(inventory["files"]) <= 4096
    )
    candidates = (
        [output / "manifest.json", *sorted(output.glob("part-*.gz"))]
        if entrypoint == "wav-catalog"
        else [output / "offsite-manifest.json", *sorted(output.glob("transport-*.gz"))]
    )
    need(len(candidates) == len(inventory["files"]))
    frozen.mkdir(mode=0o700)
    sealed = []
    for index, path in enumerate(candidates):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as body:
            before = os.fstat(body.fileno())
            need(
                stat.S_ISREG(before.st_mode)
                and before.st_uid == 0
                and before.st_nlink == 1
                and not before.st_mode & 0o077
                and 0 < before.st_size <= 64 * 1024**2
            )
            destination = frozen / f"input-{index:04d}.bin"
            output_fd = os.open(
                destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400
            )
            with os.fdopen(output_fd, "wb") as target:
                os.fchmod(target.fileno(), 0o400)
                shutil.copyfileobj(body, target, length=65536)
                target.flush()
                os.fsync(target.fileno())
            after = os.fstat(body.fileno())
            need(
                (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                )
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            )
        content = destination.read_bytes()
        sealed.append({"path": str(destination), "sha256": digest(content), "bytes": len(content)})
    need(
        all(
            type(row) is dict
            and set(row) == {"sha256", "bytes"}
            and type(row["sha256"]) is str
            and re.fullmatch(r"[a-f0-9]{64}", row["sha256"])
            and type(row["bytes"]) is int
            and 0 < row["bytes"] <= 64 * 1024**2
            for row in inventory["files"]
        )
    )
    need(
        sorted((row["sha256"], row["bytes"]) for row in sealed)
        == sorted((row["sha256"], row["bytes"]) for row in inventory["files"])
    )
    os.chmod(frozen, 0o500)
    fd = os.open(frozen, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return sealed


def run(scope_path, scope_sha):
    import importlib

    need("app" not in sys.modules and not any(name.startswith("app.") for name in sys.modules))
    scope = json.loads(root_file(scope_path, scope_sha))
    fields = {
        "format",
        "scope_epoch",
        "source_hostname",
        "source_machine_sha256",
        "unit",
        "runtime_root",
        "manifest_sha256",
        "runtime_sha256",
        "launcher_path",
        "launcher_sha256",
        "code_scope_path",
        "code_scope_sha256",
        "startup_path",
        "audit_path",
        "handoff_path",
        "import_proof_path",
        "input_manifest_path",
        "input_manifest_sha256",
        "restore_plan_path",
        "restore_plan_sha256",
        "wait_seconds",
    }
    need(
        type(scope) is dict
        and set(scope) == fields
        and scope["format"] == "greenmind.managed-snapshot.restore-launch-scope.v1"
    )
    need(re.fullmatch(r"[a-f0-9]{32}", scope["scope_epoch"]))
    for key in (
        "source_machine_sha256",
        "manifest_sha256",
        "runtime_sha256",
        "launcher_sha256",
        "code_scope_sha256",
        "input_manifest_sha256",
        "restore_plan_sha256",
    ):
        need(type(scope[key]) is str and re.fullmatch(r"[a-f0-9]{64}", scope[key]))
    need(
        socket.gethostname() == scope["source_hostname"]
        and digest(Path("/etc/machine-id").read_bytes()) == scope["source_machine_sha256"]
    )
    launcher = Path(scope["launcher_path"])
    need(
        launcher == Path(__file__).resolve()
        and digest(root_file(launcher, scope["launcher_sha256"])) == scope["launcher_sha256"]
    )
    need(type(scope["wait_seconds"]) is int and 1 <= scope["wait_seconds"] <= 300)
    invocation = os.environ.get("INVOCATION_ID", "")
    startup, owner = consumer_identity(scope["unit"], invocation)
    audit_runtime(scope["runtime_root"], scope["manifest_sha256"], scope["runtime_sha256"])
    root_file(scope["code_scope_path"], scope["code_scope_sha256"])
    inventory = json.loads(root_file(scope["input_manifest_path"], scope["input_manifest_sha256"]))
    plan = json.loads(root_file(scope["restore_plan_path"], scope["restore_plan_sha256"]))
    plan_fields = {
        "format",
        "entrypoint",
        "reference_path",
        "reference_sha256",
        "output_path",
        "frozen_dir",
        "frozen_manifest_path",
    }
    need(
        type(plan) is dict
        and set(plan) == plan_fields
        and plan["format"] == "greenmind.managed-snapshot.restore-plan.v1"
        and plan["entrypoint"] in {"raw-restore", "wav-catalog"}
    )
    root_file(plan["reference_path"], plan["reference_sha256"])
    for key in ("output_path", "frozen_dir", "frozen_manifest_path"):
        path = Path(plan[key])
        need(
            type(plan[key]) is str
            and path.is_absolute()
            and path.resolve(strict=False) == path
            and not path.exists()
            and not path.is_symlink()
        )
        for parent in path.parents:
            info = parent.stat(follow_symlinks=False)
            need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    need(len({plan["output_path"], plan["frozen_dir"], plan["frozen_manifest_path"]}) == 3)
    create_root_file(scope["startup_path"], startup)
    audit = {
        "format": "greenmind.managed-snapshot.preimport-audit.v1",
        "scope_epoch": scope["scope_epoch"],
        "owner": owner,
        "runtime_root": scope["runtime_root"],
        "manifest_sha256": scope["manifest_sha256"],
        "runtime_sha256": scope["runtime_sha256"],
        "launcher_path": scope["launcher_path"],
        "launcher_sha256": scope["launcher_sha256"],
    }
    audit_sha = create_root_file(scope["audit_path"], audit)
    deadline = time.monotonic() + scope["wait_seconds"]
    handoff_path = Path(scope["handoff_path"])
    while not handoff_path.exists():
        need(time.monotonic() < deadline)
        time.sleep(0.1)
    handoff = json.loads(read_root_handoff(handoff_path))
    need(
        type(handoff) is dict
        and set(handoff) == {"receipt_path", "receipt_sha256", "operation_path", "operation_sha256"}
    )
    operation_raw = root_file(handoff["operation_path"], handoff["operation_sha256"])
    operation = json.loads(operation_raw)
    receipt = json.loads(root_file(handoff["receipt_path"], handoff["receipt_sha256"]))
    target = {"entrypoint": plan["entrypoint"], "output_path": plan["output_path"]}
    if plan["entrypoint"] == "raw-restore":
        target["gateway"] = os.environ["RAW_ARCHIVE_RESTORE_GATEWAY_DATABASE_URL"]
        target["direct"] = os.environ["RAW_ARCHIVE_RESTORE_DIRECT_DATABASE_URL"]
    target_sha = digest(json.dumps(target, sort_keys=True, separators=(",", ":")).encode())
    need(
        type(operation) is dict
        and type(receipt) is dict
        and receipt.get("schema") == 1
        and receipt.get("state") == "active"
        and receipt.get("kind") == "restoreinput"
        and receipt.get("consumer_entrypoint") == plan["entrypoint"]
        and receipt.get("scope_epoch") == scope["scope_epoch"]
        and receipt.get("owner") == owner
        and receipt.get("operation_sha256") == handoff["operation_sha256"]
        and receipt.get("input_sha256") == scope["input_manifest_sha256"]
        and operation.get("pin_id") == receipt.get("pin_id")
        and operation.get("snapshot_id") == receipt.get("snapshot_id")
        and operation.get("owner") == owner
        and operation.get("kind") == "restoreinput"
        and operation.get("scope_epoch") == scope["scope_epoch"]
        and operation.get("input_sha256") == scope["input_manifest_sha256"]
        and operation.get("restore_plan_sha256") == scope["restore_plan_sha256"]
        and operation.get("target_sha256") == target_sha
        and operation.get("consumer_entrypoint") == plan["entrypoint"]
        and type(receipt.get("snapshot_name")) is str
        and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", receipt["snapshot_name"])
    )
    audit_runtime(scope["runtime_root"], scope["manifest_sha256"], scope["runtime_sha256"])
    need(consumer_identity(scope["unit"], invocation) == (startup, owner))
    need("app" not in sys.modules and not any(name.startswith("app.") for name in sys.modules))
    os.environ.update(
        RAW_ARCHIVE_MANAGED_SCOPE_REQUIRED="true",
        RAW_ARCHIVE_MANAGED_SCOPE_EPOCH=scope["scope_epoch"],
        RAW_ARCHIVE_MANAGED_PIN_RECEIPT_FILE=handoff["receipt_path"],
        RAW_ARCHIVE_MANAGED_PIN_RECEIPT_SHA256=handoff["receipt_sha256"],
        RAW_ARCHIVE_MANAGED_OPERATION_SHA256=handoff["operation_sha256"],
        RAW_ARCHIVE_OPERATION_UNIT=scope["unit"],
        RAW_ARCHIVE_MANAGED_CODE_SCOPE_FILE=scope["code_scope_path"],
        RAW_ARCHIVE_MANAGED_CODE_SCOPE_SHA256=scope["code_scope_sha256"],
        RAW_ARCHIVE_MANAGED_STARTUP_PROOF_FILE=scope["audit_path"],
        RAW_ARCHIVE_MANAGED_STARTUP_PROOF_SHA256=audit_sha,
        RAW_ARCHIVE_MANAGED_IMPORT_PROOF_FILE=scope["import_proof_path"],
    )
    sys.path.insert(0, str(Path(scope["runtime_root"]) / "backend"))
    module_name = (
        "app.raw_archive.recovery"
        if plan["entrypoint"] == "raw-restore"
        else "app.raw_archive.wav_catalog"
    )
    sys.argv = [
        module_name,
        "--restore-reference",
        plan["reference_path"],
        "--output",
        plan["output_path"],
        "--snapshot",
        receipt["snapshot_name"],
    ]
    importlib.import_module(module_name).main()
    sealed = seal_restore_inputs(
        plan["output_path"], plan["frozen_dir"], inventory, plan["entrypoint"]
    )
    create_root_file(
        plan["frozen_manifest_path"],
        {
            "schema": 1,
            "pin_id": operation["pin_id"],
            "snapshot_id": operation["snapshot_id"],
            "operation_sha256": handoff["operation_sha256"],
            "local_only": True,
            "files": sealed,
            "target_sha256": target_sha,
            "restore_plan_sha256": scope["restore_plan_sha256"],
            "local_runtime_sha256": scope["runtime_sha256"],
            "local_executor": "app.raw_archive.recovery.restore_bundle"
            if plan["entrypoint"] == "raw-restore"
            else "app.raw_archive.wav_catalog.restore_bundle",
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    run(args.scope, args.sha256)


if __name__ == "__main__":
    main()
