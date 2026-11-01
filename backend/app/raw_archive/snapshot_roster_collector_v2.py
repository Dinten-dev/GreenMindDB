"""Fail-closed, root-local managed-snapshot consumer inventory.

No network, no new connections, no service writes. Run only with an independently
SHA-pinned machine-specific plan. Unproved runtime module bindings remain HOLD.
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import signal
import socket
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PLAN_FORMAT = "greenmind.managed-snapshot.root-roster-plan.v2"
ROSTER_FORMAT = "greenmind.managed-snapshot.consumer-roster.v2"
JOURNAL_FORMAT = "greenmind.managed-snapshot.root-operations.v1"
SHA = re.compile(r"[0-9a-f]{64}\Z")
EPOCH = re.compile(r"[0-9a-f]{32}\Z")
UNIT = re.compile(r"[A-Za-z0-9_.@:-]+\.(?:service|timer)\Z")
RELEVANT = re.compile(r"greenmind|tiered|archive|feature[-_]?backup|restore|pilot", re.I)
ALLOWED_ENV = ("RAW_ARCHIVE_MANAGED_SCOPE_REQUIRED", "RAW_ARCHIVE_MANAGED_SCOPE_EPOCH")
PHASE = "startup"


def need(value):
    if not value:
        raise ValueError("root_roster_closed")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def trusted_parents(path):
    need(path.is_absolute() and ".." not in path.parts)
    for parent in reversed(path.parents):
        i = parent.lstat()
        need(stat.S_ISDIR(i.st_mode) and i.st_uid == 0 and not i.st_mode & 0o022)


def file_info(path, *, exact_mode=None, max_bytes=128 * 1024 * 1024, contents=False):
    path = Path(path)
    trusted_parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        before = os.fstat(f.fileno())
        need(
            stat.S_ISREG(before.st_mode)
            and before.st_uid == 0
            and before.st_nlink == 1
            and not before.st_mode & 0o022
            and before.st_size <= max_bytes
        )
        need(exact_mode is None or stat.S_IMODE(before.st_mode) == exact_mode)
        h = hashlib.sha256()
        pieces = []
        while True:
            b = f.read(65536)
            if not b:
                break
            h.update(b)
            if contents:
                pieces.append(b)
        after = os.fstat(f.fileno())
    fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_uid",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    need(all(getattr(before, k) == getattr(after, k) for k in fields))
    info = {
        "path": str(path),
        "sha256": h.hexdigest(),
        "uid": before.st_uid,
        "mode": stat.S_IMODE(before.st_mode),
        "device": before.st_dev,
        "inode": before.st_ino,
    }
    return (info, b"".join(pieces)) if contents else info


def load_plan(path, expected):
    need(os.geteuid() == 0 and isinstance(expected, str) and SHA.fullmatch(expected))
    info, raw = file_info(path, exact_mode=0o400, max_bytes=1048576, contents=True)
    need(info["sha256"] == expected)
    obj = json.loads(raw)
    keys = {
        "format",
        "role",
        "hostname",
        "machine_sha256",
        "boot_id",
        "scope_epoch",
        "collector_sha256",
        "runtime",
        "units",
        "journal_roots",
        "journals",
        "new_snapshot_ids",
        "source_lease_profile",
        "output_path",
        "dependency_roots",
    }
    need(
        type(obj) is dict
        and set(obj) == keys
        and obj["format"] == PLAN_FORMAT
        and obj["role"] in ("source", "archive")
    )
    need(type(obj["hostname"]) is str and obj["hostname"] == socket.gethostname())
    need(
        type(obj["machine_sha256"]) is str
        and SHA.fullmatch(obj["machine_sha256"])
        and sha(Path("/etc/machine-id").read_bytes()) == obj["machine_sha256"]
    )
    need(type(obj["scope_epoch"]) is str and EPOCH.fullmatch(obj["scope_epoch"]))
    need(
        obj["boot_id"] == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        and re.fullmatch(r"[0-9a-f-]{36}", obj["boot_id"])
    )
    self_info = file_info(Path(__file__).resolve(), exact_mode=0o400)
    need(self_info["sha256"] == obj["collector_sha256"])
    need(type(obj["units"]) is list and obj["units"] and len(obj["units"]) <= 512)
    need(
        type(obj["journal_roots"]) is list
        and obj["journal_roots"]
        and len(obj["journal_roots"]) <= 16
    )
    need(type(obj["journals"]) is list and len(obj["journals"]) <= 1024)
    need(
        type(obj["new_snapshot_ids"]) is list
        and len(obj["new_snapshot_ids"]) <= 128
        and all(type(v) is int and v > 0 for v in obj["new_snapshot_ids"])
        and len(set(obj["new_snapshot_ids"])) == len(obj["new_snapshot_ids"])
    )
    need(type(obj["dependency_roots"]) is list and 1 <= len(obj["dependency_roots"]) <= 16)
    return obj


def command(args):
    p = subprocess.run(  # noqa: S603 -- callers use fixed systemctl and validated unit names.
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=8,
        check=False,
    )
    need(p.returncode == 0 and len(p.stdout) <= 1048576)
    return p.stdout.decode("utf-8", errors="strict")


def relevant(name):
    return bool(UNIT.fullmatch(name) and (RELEVANT.search(name) or name.startswith("gm-")))


def discover_units():
    # Both installed and loaded transient unit lists are sampled; unknown matches hold.
    seen = set()
    for argv in (
        [
            "/usr/bin/systemctl",
            "list-unit-files",
            "--all",
            "--no-legend",
            "--no-pager",
            "--type=service,timer",
        ],
        [
            "/usr/bin/systemctl",
            "list-units",
            "--all",
            "--plain",
            "--no-legend",
            "--no-pager",
            "--type=service,timer",
        ],
    ):
        for row in command(argv).splitlines():
            words = row.split()
            if words and relevant(words[0]):
                seen.add(words[0])
    return sorted(seen)


def systemd_unit(name):
    need(UNIT.fullmatch(name))
    props = "Id,LoadState,ActiveState,UnitFileState,InvocationID,MainPID,ControlGroup,FragmentPath,DropInPaths,EnvironmentFiles,Environment,ExecStart"
    raw = command(["/usr/bin/systemctl", "show", name, "--no-pager", "--property=" + props])
    pairs = [line.split("=", 1) for line in raw.splitlines()]
    need(all(len(v) == 2 for v in pairs))
    value = dict(pairs)
    expected = set(props.split(","))
    # Some systemd versions omit service-only properties on timers.
    need(
        set(value) <= expected
        and {"Id", "LoadState", "ActiveState", "UnitFileState", "FragmentPath", "DropInPaths"}
        <= set(value)
        and len(value) == len(pairs)
        and value["Id"] == name
    )
    return value


def linux_generation(pid):
    raw = Path("/proc", str(pid), "stat").read_text()
    fields = raw.rsplit(")", 1)[1].split()
    need(len(fields) >= 20)
    return int(fields[19])


def linux_uid(pid):
    lines = Path("/proc", str(pid), "status").read_text().splitlines()
    fields = [line.split()[1:] for line in lines if line.startswith("Uid:")]
    need(len(fields) == 1 and len(fields[0]) == 4)
    return int(fields[0][1])


def linux_cgroup(pid):
    rows = Path("/proc", str(pid), "cgroup").read_text().splitlines()
    values = [x[3:] for x in rows if x.startswith("0::")]
    need(len(values) == 1)
    return values[0]


def env_paths(raw):
    # systemctl prints each path followed by '(ignore_errors=yes|no)'.
    if not raw.strip():
        return []
    tokens = shlex.split(raw)
    need(len(tokens) % 2 == 0)
    paths = []
    for path, flag in zip(tokens[::2], tokens[1::2], strict=True):
        need(flag in ("(ignore_errors=yes)", "(ignore_errors=no)") and path.startswith("/"))
        paths.append(path)
    need(len(paths) == len(set(paths)))
    return paths


def env_allowlist(raw):
    values = {key: None for key in ALLOWED_ENV}
    for item in raw.split(b"\0"):
        if b"=" not in item:
            continue
        k, v = item.split(b"=", 1)
        try:
            name = k.decode("ascii")
        except UnicodeError:
            continue
        if name in values:
            need(values[name] is None)
            values[name] = v.decode("utf-8", errors="strict")
    return values


def configured_env(show, files):
    # EnvironmentFile values override Environment; accept simple assignments only.
    values = {k: None for k in ALLOWED_ENV}
    for item in shlex.split(show.get("Environment", "")):
        if "=" in item:
            key, value = item.split("=", 1)
            if key in values:
                values[key] = value
    for path in files:
        _, raw = file_info(path, max_bytes=1048576, contents=True)
        for line in raw.decode("utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", ";")):
                continue
            if "=" not in stripped:
                raise ValueError("unsupported_environment_syntax")
            key, value = stripped.split("=", 1)
            if key in values:
                parts = shlex.split(value, comments=False)
                need(len(parts) <= 1)
                values[key] = parts[0] if parts else ""
    return values


def checked_array(paths, expected, *, modules=False):
    need(type(expected) is list and len(expected) <= 256)
    infos = [file_info(path) for path in paths]
    if modules:
        need(len(paths) == len(expected))
        infos = [dict(name=expected[i]["name"], **info) for i, info in enumerate(infos)]
        need(all(type(x["name"]) is str and 1 <= len(x["name"]) <= 128 for x in infos))
    need(infos == expected)
    return infos


def runtime(plan):
    need(
        type(plan) is dict
        and set(plan) == {"root", "manifest_sha256", "runtime_sha256", "file_count"}
        and type(plan["file_count"]) is int
        and 1 <= plan["file_count"] <= 2048
    )
    root = Path(plan["root"])
    trusted_parents(root / "x")
    root_info = root.lstat()
    need(
        stat.S_ISDIR(root_info.st_mode)
        and root_info.st_uid == 0
        and stat.S_IMODE(root_info.st_mode) == 0o555
    )
    manifest_info, raw = file_info(root / "manifest.json", max_bytes=1048576, contents=True)
    need(manifest_info["sha256"] == plan["manifest_sha256"])
    data = json.loads(raw)
    need(
        data["runtime_sha256"] == plan["runtime_sha256"]
        and len(data["files"]) == plan["file_count"]
    )
    observed = set()
    for current, dirs, names in os.walk(root, followlinks=False):
        for name in dirs:
            directory = Path(current) / name
            info = directory.lstat()
            need(
                stat.S_ISDIR(info.st_mode)
                and info.st_uid == 0
                and stat.S_IMODE(info.st_mode) == 0o555
            )
        for name in names:
            file = Path(current) / name
            observed.add(file.relative_to(root).as_posix())
            need(stat.S_IMODE(file.lstat().st_mode) == 0o444)
    need(observed == set(data["files"]) | {"manifest.json"})
    for name, expected in data["files"].items():
        part = Path(name)
        need(
            not part.is_absolute()
            and ".." not in part.parts
            and file_info(root / part)["sha256"] == expected
        )
    backend = root / "backend"
    files = sorted(
        [
            *(backend / "app").rglob("*.py"),
            *(backend / "app").rglob("*.sql"),
            backend / "requirements.lock",
            backend / "requirements-archive.lock",
        ]
    )
    h = hashlib.sha256()
    for path in files:
        _, content = file_info(path, max_bytes=8 * 1024 * 1024, contents=True)
        h.update(path.relative_to(backend).as_posix().encode() + b"\0")
        h.update(content)
    need(h.hexdigest() == plan["runtime_sha256"])
    return h.hexdigest()


def inventory_unit(item, epoch):
    keys = {
        "name",
        "unit_files",
        "environment_files",
        "dropin_files",
        "modules",
        "entrypoint",
        "execstart_sha256",
        "executable_path",
        "executable_sha256",
        "legacy_latest_files",
    }
    need(type(item) is dict and set(item) == keys and UNIT.fullmatch(item["name"]))
    name = item["name"]
    before = systemd_unit(name)
    need(before["LoadState"] == "loaded")
    fragments = [before["FragmentPath"]] if before["FragmentPath"] else []
    unit_files = checked_array(fragments, item["unit_files"])
    dropin_files = checked_array(shlex.split(before["DropInPaths"]), item["dropin_files"])
    environment_paths = env_paths(before.get("EnvironmentFiles", ""))
    environment_files = checked_array(environment_paths, item["environment_files"])
    modules = checked_array([m["path"] for m in item["modules"]], item["modules"], modules=True)
    pid = int(before.get("MainPID", "0") or "0")
    invocation = before.get("InvocationID", "")
    group = before.get("ControlGroup", "")
    ticks = 0
    uid = None
    executable_path = ""
    executable_sha256 = ""
    scope_env = configured_env(before, environment_paths)
    if pid:
        need(
            before["ActiveState"] in ("active", "activating")
            and re.fullmatch("[0-9a-f]{32}", invocation)
            and group.startswith("/")
        )
        ticks = linux_generation(pid)
        uid = linux_uid(pid)
        need(linux_cgroup(pid) == group)
        exe = Path("/proc", str(pid), "exe").resolve(strict=True)
        executable = file_info(exe)
        need(
            str(exe) == item["executable_path"]
            and executable["sha256"] == item["executable_sha256"]
        )
        argv_raw = Path("/proc", str(pid), "cmdline").read_bytes()
        need(len(argv_raw) <= 65536)
        argv = argv_raw.split(b"\0")
        need(os.fsencode(item["entrypoint"]) in argv)
        env_raw = Path("/proc", str(pid), "environ").read_bytes()
        need(len(env_raw) <= 262144)
        actual = env_allowlist(env_raw)
        scope_env = actual
        executable_path, executable_sha256 = str(exe), executable["sha256"]
    elif name.endswith(".timer"):
        need(
            not group
            and (not invocation or re.fullmatch("[0-9a-f]{32}", invocation))
            and before["ActiveState"] in ("active", "inactive", "failed")
        )
    else:
        need(not invocation and not group and before["ActiveState"] in ("inactive", "failed"))
    need(sha(before.get("ExecStart", "").encode()) == item["execstart_sha256"])
    # Only these two scope values leave the process. Validate their domains.
    need(scope_env[ALLOWED_ENV[0]] in (None, "true", "false"))
    need(scope_env[ALLOWED_ENV[1]] is None or EPOCH.fullmatch(scope_env[ALLOWED_ENV[1]]))
    after = systemd_unit(name)
    need(before == after)
    if pid:
        need(
            linux_generation(pid) == ticks and linux_uid(pid) == uid and linux_cgroup(pid) == group
        )
    checked_array(fragments, item["unit_files"])
    checked_array(environment_paths, item["environment_files"])
    checked_array(shlex.split(before["DropInPaths"]), item["dropin_files"])
    checked_array([m["path"] for m in item["modules"]], item["modules"], modules=True)
    return {
        "name": name,
        "load_state": before["LoadState"],
        "active_state": before["ActiveState"],
        "unit_file_state": before["UnitFileState"],
        "invocation": invocation,
        "main_pid": pid,
        "main_start_ticks": ticks,
        "main_uid": uid,
        "cgroup": group,
        "unit_files": unit_files,
        "environment_files": environment_files,
        "dropin_files": dropin_files,
        "executable_path": executable_path,
        "executable_sha256": executable_sha256,
        "scope_environment": scope_env,
        "modules": modules,
        "modules_binding": "configured_only",
    }


def discover_journals(roots):
    found = []
    for entry in roots:
        need(
            type(entry) is dict
            and set(entry) == {"path", "name_regex"}
            and type(entry["name_regex"]) is str
            and 1 <= len(entry["name_regex"]) <= 128
        )
        root = Path(entry["path"])
        trusted_parents(root / "x")
        info = root.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
        regex = re.compile(entry["name_regex"])
        count = 0
        for current, dirs, files in os.walk(root, followlinks=False):
            need(len(Path(current).relative_to(root).parts) <= 8)
            for name in dirs:
                child = Path(current) / name
                metadata = child.lstat()
                need(
                    stat.S_ISDIR(metadata.st_mode)
                    and metadata.st_uid == 0
                    and not metadata.st_mode & 0o022
                )
            for name in files:
                count += 1
                need(count <= 10000)
                if regex.fullmatch(name):
                    found.append(str(Path(current) / name))
    need(len(found) == len(set(found)))
    return sorted(found)


def journals(plan):
    expected = plan["journals"]
    need(discover_journals(plan["journal_roots"]) == sorted(x["path"] for x in expected))
    listed = []
    operations = []
    for item in expected:
        need(
            type(item) is dict
            and set(item) == {"path", "device", "inode", "owner", "mode", "schema"}
        )
        info, raw = file_info(item["path"], max_bytes=1048576, contents=True)
        need(
            (info["device"], info["inode"], info["uid"], info["mode"])
            == (item["device"], item["inode"], item["owner"], item["mode"])
            and item["schema"] == JOURNAL_FORMAT
        )
        value = json.loads(raw)
        need(
            type(value) is dict
            and set(value) == {"format", "operations"}
            and value["format"] == JOURNAL_FORMAT
            and type(value["operations"]) is list
            and len(value["operations"]) <= 4096
        )
        safe_rows = []
        for row in value["operations"]:
            need(
                type(row) is dict
                and set(row)
                == {"pin_id", "snapshot_id", "scope_epoch", "operation_sha256", "owner", "state"}
            )
            need(
                type(row["snapshot_id"]) is int
                and row["snapshot_id"] > 0
                and EPOCH.fullmatch(row["pin_id"])
                and EPOCH.fullmatch(row["scope_epoch"])
                and SHA.fullmatch(row["operation_sha256"])
                and row["state"] in ("active", "uncertain", "complete", "expired")
            )
            owner = row["owner"]
            need(
                type(owner) is dict
                and set(owner) == {"host", "unit", "invocation", "pid_generation"}
                and all(type(x) is str and 1 <= len(x) <= 256 for x in owner.values())
            )
            need(
                re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", owner["host"])
                and UNIT.fullmatch(owner["unit"])
                and re.fullmatch("[0-9a-f]{32}", owner["invocation"])
                and re.fullmatch("[1-9][0-9]*:[1-9][0-9]*", owner["pid_generation"])
            )
            safe_rows.append(row)
            if row["snapshot_id"] in plan["new_snapshot_ids"] and row["state"] in (
                "active",
                "uncertain",
            ):
                operations.append(
                    {
                        k: row[k]
                        for k in ("pin_id", "snapshot_id", "operation_sha256", "owner", "state")
                    }
                    | {
                        "journal_path": item["path"],
                        "journal_device": item["device"],
                        "journal_inode": item["inode"],
                    }
                )
        need(file_info(item["path"])["sha256"] == info["sha256"])
        listed.append({**info, "schema": item["schema"], "operations": safe_rows})
    return listed, operations


def collect(plan, plan_sha):
    global PHASE
    PHASE = "runtime"
    runtime_sha = runtime(plan["runtime"])
    PHASE = "lease_profile"
    lease = file_info(plan["source_lease_profile"]["path"])
    need(lease == plan["source_lease_profile"])
    PHASE = "unit_discovery"
    before = discover_units()
    planned = [u["name"] for u in plan["units"]]
    need(len(planned) == len(set(planned)))
    # Do not suppress unknown units by accepting the plan's own claims.
    unknown = sorted(set(before) - set(planned))
    missing = sorted(set(planned) - set(before))
    reasons = []
    if unknown:
        reasons.append("unknown_relevant_units")
    if missing:
        reasons.append("planned_units_missing")
    # Static discovery cannot prove arbitrary dynamically constructed latest selectors.
    reasons.append("legacy_latest_coverage_unproven")
    units = []
    legacy = []
    PHASE = "unit_evidence"
    for item in plan["units"]:
        if item["name"] not in before:
            continue
        unit = inventory_unit(item, plan["scope_epoch"])
        units.append(unit)
        active = unit["main_pid"] > 0
        automatic = unit["unit_file_state"] in (
            "enabled",
            "enabled-runtime",
            "linked",
            "linked-runtime",
        )
        if item["name"].endswith(".service"):
            if unit["scope_environment"] != {
                ALLOWED_ENV[0]: "true",
                ALLOWED_ENV[1]: plan["scope_epoch"],
            }:
                reasons.append("unfenced_relevant_unit")
            # On-disk Python code hashes do not prove the actual loaded module set.
            reasons.append("loaded_module_binding_unproven")
        need(type(item["legacy_latest_files"]) is list)
        discovered_latest = []
        for module in item["modules"]:
            _, code = file_info(module["path"], max_bytes=8 * 1024 * 1024, contents=True)
            if re.search(rb"latest", code, re.IGNORECASE):
                discovered_latest.append(module["path"])
        need(sorted(item["legacy_latest_files"]) == sorted(discovered_latest))
        for path in discovered_latest:
            info = next(x for x in item["modules"] if x["path"] == path)
            legacy.append(
                {
                    "unit": item["name"],
                    "file": path,
                    "sha256": info["sha256"],
                    "enabled": automatic,
                    "active": active,
                    "fence_sha256": None,
                }
            )
            if active or automatic:
                reasons.append("legacy_latest_fence_unproven")
    PHASE = "root_journals"
    listed, operations = journals(plan)
    PHASE = "dependency_roots"
    dependency_matches = discover_journals(plan["dependency_roots"])
    need(not dependency_matches)
    if operations:
        reasons.append("active_or_uncertain_new_snapshot_operations")
    PHASE = "final_stability"
    need(
        discover_units() == before
        and discover_journals(plan["journal_roots"]) == sorted(x["path"] for x in listed)
    )
    need(discover_journals(plan["dependency_roots"]) == dependency_matches)
    for journal in listed:
        need(file_info(journal["path"])["sha256"] == journal["sha256"])
    for item, previous in zip((x for x in plan["units"] if x["name"] in before), units, strict=True):
        need(inventory_unit(item, plan["scope_epoch"]) == previous)
    value = {
        "format": ROSTER_FORMAT,
        "status": "HOLD" if reasons else "PASS",
        "reasons": sorted(set(reasons)),
        "unknown_units": unknown,
        "missing_units": missing,
        "plan_sha256": plan_sha,
        "scope_epoch": plan["scope_epoch"],
        "role": plan["role"],
        "hostname": plan["hostname"],
        "machine_sha256": plan["machine_sha256"],
        "boot_id": plan["boot_id"],
        "observed_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "collector_sha256": plan["collector_sha256"],
        "runtime_sha256": runtime_sha,
        "source_lease_profile": lease,
        "units": units,
        "root_operations": operations,
        "root_operation_journals": listed,
        "legacy_latest_snapshot_consumers": legacy,
    }
    value["dependency_matches"] = dependency_matches
    return value


def write_proof(path, value):
    path = Path(path)
    trusted_parents(path)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, "wb") as f:
        os.fchmod(f.fileno(), 0o400)
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return sha(raw)


def main():
    global PHASE
    p = argparse.ArgumentParser()
    p.add_argument("--plan", required=True)
    p.add_argument("--plan-sha256", required=True)
    args = p.parse_args()
    PHASE = "plan"
    plan = load_plan(args.plan, args.plan_sha256)
    roster = collect(plan, args.plan_sha256)
    PHASE = "output"
    digest = write_proof(plan["output_path"], roster)
    print(
        json.dumps(
            {"status": roster["status"], "reasons": roster["reasons"], "roster_sha256": digest},
            sort_keys=True,
        )
    )
    return 0 if roster["status"] == "PASS" else 2


if __name__ == "__main__":

    def deadline(_sig, _frame):
        raise TimeoutError("root_roster_deadline")

    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(75)
    try:
        raise SystemExit(main())
    except Exception as error:
        print(
            json.dumps(
                {"status": "HOLD", "phase": PHASE, "exception": type(error).__name__},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
