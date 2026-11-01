"""Explicit operator Storage Box API access and journal-guarded managed rotation."""

import argparse
import hashlib
import itertools
import json
import os
import re
import stat
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

from .policy import ArchiveBlocked, checksum
from .recovery import private_directory

API = "https://api.hetzner.com/v1"
_COMPILED_CODE_CACHE = {}
_RUNTIME_AUDIT_CACHE = {}


def check_loaded_function_code(module, path, source, functions):
    """Compare actual loaded function code objects with the pinned source."""
    cache_key = (str(path), hashlib.sha256(source).hexdigest(), sys.flags.optimize)
    known = _COMPILED_CODE_CACHE.get(cache_key)
    if known is None:
        compiled = compile(
            source, str(path), "exec", dont_inherit=True, optimize=sys.flags.optimize
        )
        pending = [compiled]
        known = {}
        while pending:
            code = pending.pop()
            known[(code.co_qualname, code.co_firstlineno)] = code
            pending.extend(value for value in code.co_consts if isinstance(value, types.CodeType))
        if len(_COMPILED_CODE_CACHE) >= 32:
            raise ArchiveBlocked("Loaded code cache exceeds its budget")
        _COMPILED_CODE_CACHE[cache_key] = known
    result = {}
    for qualified in functions:
        value = module
        for part in qualified.split("."):
            value = getattr(value, part, None)
        actual = getattr(value, "__code__", None)
        if (
            not isinstance(actual, types.CodeType)
            or known.get((actual.co_qualname, actual.co_firstlineno)) != actual
        ):
            raise ArchiveBlocked("Loaded snapshot consumer bytecode differs")
        result[qualified] = actual
    return result


def capture_managed_import_proof(receipt):
    """Attest live entrypoint/code identities, after an independently pinned pre-import audit."""
    from app.tiered_archive.ledger import _private_read

    entries = {
        "raw-pilot": ("app.raw_archive.pilot", "run_pilot"),
        "raw-restore": ("app.raw_archive.recovery", "restore_bundle"),
        "wav-catalog": ("app.raw_archive.wav_catalog", "restore_bundle"),
        "snapshot-readback": ("app.raw_archive.snapshot_api", "readback"),
    }
    entry = receipt.get("consumer_entrypoint")
    if entry not in entries or os.geteuid() != 0:
        raise ArchiveBlocked("Reviewed root snapshot consumer entrypoint required")
    scope_path = os.environ.get("RAW_ARCHIVE_MANAGED_CODE_SCOPE_FILE", "")
    scope_sha = os.environ.get("RAW_ARCHIVE_MANAGED_CODE_SCOPE_SHA256", "")
    startup_path = os.environ.get("RAW_ARCHIVE_MANAGED_STARTUP_PROOF_FILE", "")
    startup_sha = os.environ.get("RAW_ARCHIVE_MANAGED_STARTUP_PROOF_SHA256", "")
    output_path = Path(os.environ.get("RAW_ARCHIVE_MANAGED_IMPORT_PROOF_FILE", ""))
    if (
        not re.fullmatch(r"[a-f0-9]{64}", scope_sha)
        or not re.fullmatch(r"[a-f0-9]{64}", startup_sha)
        or not output_path.is_absolute()
        or output_path.is_symlink()
    ):
        raise ArchiveBlocked("Pinned startup audit and import proof paths required")
    scope_raw = _private_read(scope_path, 1024 * 1024)
    startup_raw = _private_read(startup_path, 1024 * 1024)
    if (
        hashlib.sha256(scope_raw).hexdigest() != scope_sha
        or hashlib.sha256(startup_raw).hexdigest() != startup_sha
    ):
        raise ArchiveBlocked("Startup audit or code scope differs")
    for pinned in (Path(scope_path), Path(startup_path)):
        info = pinned.stat(follow_symlinks=False)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o400 or info.st_nlink != 1:
            raise ArchiveBlocked("Private root startup evidence required")
    try:
        scope, startup = json.loads(scope_raw), json.loads(startup_raw)
    except ValueError:
        raise ArchiveBlocked("Startup audit evidence is invalid") from None
    scope_keys = {
        "schema",
        "scope_epoch",
        "runtime_root",
        "manifest_sha256",
        "runtime_sha256",
        "launcher_path",
        "launcher_sha256",
        "modules",
    }
    startup_keys = {
        "format",
        "scope_epoch",
        "owner",
        "runtime_root",
        "manifest_sha256",
        "runtime_sha256",
        "launcher_path",
        "launcher_sha256",
    }
    if (
        type(scope) is not dict
        or set(scope) != scope_keys
        or scope["schema"] != 1
        or type(startup) is not dict
        or set(startup) != startup_keys
        or startup["format"] != "greenmind.managed-snapshot.preimport-audit.v1"
        or any(
            startup[key] != scope[key]
            for key in (
                "scope_epoch",
                "runtime_root",
                "manifest_sha256",
                "runtime_sha256",
                "launcher_path",
                "launcher_sha256",
            )
        )
        or startup["owner"] != receipt["owner"]
        or scope["scope_epoch"] != receipt["scope_epoch"]
        or any(
            type(scope[key]) is not str or not re.fullmatch(r"[a-f0-9]{64}", scope[key])
            for key in ("manifest_sha256", "runtime_sha256", "launcher_sha256")
        )
    ):
        raise ArchiveBlocked("Pre-import runtime audit binding differs")
    root = Path(scope["runtime_root"])
    if not root.is_absolute() or root.is_symlink() or root.resolve(strict=False) != root:
        raise ArchiveBlocked("Immutable runtime root required")
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or manifest_path.stat().st_size > 262144:
        raise ArchiveBlocked("Bounded runtime manifest required")
    manifest_raw = manifest_path.read_bytes()
    if hashlib.sha256(manifest_raw).hexdigest() != scope["manifest_sha256"]:
        raise ArchiveBlocked("Immutable runtime manifest differs")
    manifest = json.loads(manifest_raw)
    files = manifest.get("files") if type(manifest) is dict else None
    if (
        type(files) is not dict
        or len(files) > 100000
        or manifest.get("runtime_sha256") != scope["runtime_sha256"]
        or type(manifest.get("commit")) is not str
        or not re.fullmatch(r"[a-f0-9]{40}", manifest["commit"])
    ):
        raise ArchiveBlocked("Bounded immutable runtime manifest required")
    launcher = Path(scope["launcher_path"])
    launcher_raw = _private_read(launcher, 1024 * 1024)
    launcher_info = launcher.stat(follow_symlinks=False)
    if (
        not launcher.is_absolute()
        or launcher.is_symlink()
        or launcher_info.st_uid != 0
        or stat.S_IMODE(launcher_info.st_mode) != 0o400
        or launcher_info.st_nlink != 1
        or hashlib.sha256(launcher_raw).hexdigest() != scope["launcher_sha256"]
    ):
        raise ArchiveBlocked("Pre-import launcher is not root-pinned")

    def generation(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_uid,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    cache_key = (scope_sha, startup_sha, receipt["owner"]["pid_generation"])
    pinned = _RUNTIME_AUDIT_CACHE.get(cache_key)
    if pinned is None:
        actual = set()
        pinned = []
        for count, path in enumerate(itertools.chain((root,), root.rglob("*")), 1):
            if count > 200000:
                raise ArchiveBlocked("Runtime inventory exceeds its budget")
            info = path.lstat()
            before = generation(info)
            if stat.S_ISDIR(info.st_mode):
                if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o555:
                    raise ArchiveBlocked("Runtime directory is not sealed")
            elif stat.S_ISREG(info.st_mode):
                if (
                    info.st_uid != 0
                    or stat.S_IMODE(info.st_mode) != 0o444
                    or info.st_nlink != 1
                    or path.suffix == ".pyc"
                ):
                    raise ArchiveBlocked("Runtime file is not sealed")
                relative = path.relative_to(root).as_posix()
                actual.add(relative)
                if relative != "manifest.json":
                    if relative not in files:
                        raise ArchiveBlocked("Runtime contains changed or unlisted content")
                    digest = hashlib.sha256()
                    with path.open("rb") as body:
                        while chunk := body.read(64 * 1024):
                            digest.update(chunk)
                    if digest.hexdigest() != files[relative]:
                        raise ArchiveBlocked("Runtime contains changed or unlisted content")
            else:
                raise ArchiveBlocked("Runtime contains an untrusted filesystem object")
            if generation(path.lstat()) != before:
                raise ArchiveBlocked("Runtime file generation changed during audit")
            pinned.append((path, before))
        if actual != set(files) | {"manifest.json"}:
            raise ArchiveBlocked("Immutable runtime file inventory differs")
        from app.tiered_archive.runtime import runtime_sha256

        if runtime_sha256() != scope["runtime_sha256"]:
            raise ArchiveBlocked("Immutable runtime digest differs")
        if any(generation(path.lstat()) != before for path, before in pinned):
            raise ArchiveBlocked("Runtime file generation changed during audit")
        if len(_RUNTIME_AUDIT_CACHE) >= 4:
            raise ArchiveBlocked("Runtime audit cache exceeds its budget")
        _RUNTIME_AUDIT_CACHE[cache_key] = pinned
    elif any(generation(path.lstat()) != before for path, before in pinned):
        raise ArchiveBlocked("Runtime file generation changed after startup audit")
    modules = scope["modules"]
    if type(modules) is not list or not 1 <= len(modules) <= 16:
        raise ArchiveBlocked("Pinned loaded-module roster required")
    wanted = {
        "app.raw_archive.snapshot_api": [
            "require_managed_snapshot_access",
            "pilot_process_owner",
            "capture_managed_import_proof",
            "check_loaded_function_code",
            "require_pilot_pin_receipt",
        ],
        "app.raw_archive.storage": [
            "StorageBox._download_path",
            "StorageBox.download_snapshot",
            "StorageBox.download_recovery",
            "StorageBox.download_catalog",
        ],
    }
    module_name, function_name = entries[entry]
    wanted.setdefault(module_name, []).append(function_name)
    roster = {}
    for item in modules:
        if (
            type(item) is not dict
            or set(item) != {"name", "path", "sha256"}
            or item["name"] in roster
            or type(item["name"]) is not str
            or type(item["path"]) is not str
            or type(item["sha256"]) is not str
            or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
        ):
            raise ArchiveBlocked("Invalid pinned loaded-module roster")
        roster[item["name"]] = item
    if set(roster) != set(wanted):
        raise ArchiveBlocked("Actual loaded-module roster differs")
    evidence = []
    entry_code = None
    for name, functions in sorted(wanted.items()):
        module = sys.modules.get(name)
        main = sys.modules.get("__main__")
        if main is not None and getattr(getattr(main, "__spec__", None), "name", None) == name:
            module = main
        if module is None or type(getattr(module, "__file__", None)) is not str:
            raise ArchiveBlocked("Snapshot consumer module is not actually loaded")
        path = Path(module.__file__).resolve()
        item = roster[name]
        if str(path) != item["path"] or not path.is_relative_to(root) or path.suffix != ".py":
            raise ArchiveBlocked("Loaded snapshot consumer path differs")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != item["sha256"]:
            raise ArchiveBlocked("Loaded snapshot consumer code differs")
        code = check_loaded_function_code(module, path, body, functions)
        if name == module_name:
            entry_code = code[function_name]
        evidence.append({"name": name, "path": str(path), "sha256": item["sha256"]})
    frame = sys._getframe()
    while frame is not None and frame.f_code != entry_code:
        frame = frame.f_back
    if frame is None:
        raise ArchiveBlocked("Pinned snapshot entrypoint is not on the active call stack")
    proof = {
        "format": "greenmind.managed-snapshot.live-imports.v1",
        "scope_epoch": receipt["scope_epoch"],
        "pin_id": receipt["pin_id"],
        "operation_sha256": receipt["operation_sha256"],
        "owner": receipt["owner"],
        "entrypoint": entry,
        "code_scope_sha256": scope_sha,
        "startup_proof_sha256": startup_sha,
        "modules": evidence,
    }
    raw = json.dumps(proof, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    if output_path.resolve(strict=False) != output_path:
        raise ArchiveBlocked("Canonical import proof path required")
    for directory in output_path.parents:
        parent = directory.stat(follow_symlinks=False)
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022:
            raise ArchiveBlocked("Private import proof directory required")
    parent = output_path.parent.stat(follow_symlinks=False)
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022:
        raise ArchiveBlocked("Private import proof directory required")
    try:
        fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    except FileExistsError:
        info = output_path.stat(follow_symlinks=False)
        if (
            _private_read(output_path, 1024 * 1024) != raw
            or stat.S_IMODE(info.st_mode) != 0o400
            or info.st_nlink != 1
        ):
            raise ArchiveBlocked("Existing import proof differs") from None
    else:
        with os.fdopen(fd, "wb") as body:
            os.fchmod(body.fileno(), 0o400)
            body.write(raw)
            body.flush()
            os.fsync(body.fileno())
        directory_fd = os.open(output_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return hashlib.sha256(raw).hexdigest()


def pilot_process_owner():
    """Read the calling Linux process generation and systemd invocation."""
    try:
        pid = os.getpid()
        raw = Path("/proc/self/stat").read_text()
        fields = raw[raw.rindex(")") + 2 :].split()
        start = fields[19]
        if not start.isdecimal():
            raise ValueError
        invocation = os.environ["INVOCATION_ID"]
        unit = os.environ["RAW_ARCHIVE_OPERATION_UNIT"]
        cgroups = Path("/proc/self/cgroup").read_text().splitlines()
        if not re.fullmatch(r"[a-zA-Z0-9_.@:-]+\.service", unit) or not any(
            unit in line.split("/") for line in cgroups
        ):
            raise ValueError
        if not re.fullmatch(r"[a-f0-9]{32}", invocation):
            raise ValueError
        return {
            "host": os.uname().nodename,
            "unit": unit,
            "invocation": invocation,
            "pid_generation": f"{pid}:{start}",
        }
    except (OSError, KeyError, ValueError, IndexError):
        raise ArchiveBlocked("Managed pilot process generation is unavailable") from None


def require_managed_snapshot_access(snapshot_name):
    """Fence every configured source snapshot download before SFTP I/O."""
    if os.getenv("RAW_ARCHIVE_MANAGED_SCOPE_REQUIRED") != "true":
        return
    if os.getenv("RAW_ARCHIVE_WORKER_ROLE") == "archive":
        from .coordination import bound_job_lock

        try:
            held_path, held = bound_job_lock()
            contract_path = Path(os.environ["TIERED_ARCHIVE_SNAPSHOT_DEPENDENCIES_FILE"])
            expected = os.environ["TIERED_ARCHIVE_SNAPSHOT_DEPENDENCIES_SHA256"]
            if not contract_path.is_absolute() or contract_path.is_symlink():
                raise ValueError
            fd = os.open(contract_path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as body:
                info = os.fstat(body.fileno())
                raw = body.read(1024 * 1024 + 1)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid not in {0, os.getuid()}
                or info.st_mode & 0o077
                or len(raw) > 1024 * 1024
                or hashlib.sha256(raw).hexdigest() != expected
            ):
                raise ValueError
            binding = json.loads(raw)["coordination"]
            if (
                binding.get("path") == str(held_path)
                and binding.get("device") == held.st_dev
                and binding.get("inode") == held.st_ino
            ):
                return
        except (ArchiveBlocked, OSError, KeyError, TypeError, ValueError):
            pass
    path = Path(os.environ.get("RAW_ARCHIVE_MANAGED_PIN_RECEIPT_FILE", ""))
    expected = os.environ.get("RAW_ARCHIVE_MANAGED_PIN_RECEIPT_SHA256", "")
    operation = os.environ.get("RAW_ARCHIVE_MANAGED_OPERATION_SHA256", "")
    epoch = os.environ.get("RAW_ARCHIVE_MANAGED_SCOPE_EPOCH", "")
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not re.fullmatch(r"[a-f0-9]{64}", expected)
        or not re.fullmatch(r"[a-f0-9]{64}", operation)
        or not re.fullmatch(r"[a-f0-9]{32}", epoch)
    ):
        raise ArchiveBlocked("Managed snapshot access pin required")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as body:
            info = os.fstat(body.fileno())
            raw = body.read(1024 * 1024 + 1)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or info.st_mode & 0o077
            or len(raw) > 1024 * 1024
            or hashlib.sha256(raw).hexdigest() != expected
        ):
            raise ValueError
        receipt = json.loads(raw)
    except (OSError, ValueError):
        raise ArchiveBlocked("Managed snapshot access receipt unavailable") from None
    if (
        type(receipt) is not dict
        or receipt.get("schema") != 1
        or receipt.get("state") != "active"
        or receipt.get("snapshot_name") != snapshot_name
        or receipt.get("scope_epoch") != epoch
        or receipt.get("operation_sha256") != operation
        or receipt.get("owner") != pilot_process_owner()
        or not re.fullmatch(r"[a-f0-9]{32}", str(receipt.get("pin_id", "")))
        or not re.fullmatch(r"[a-f0-9]{32}", str(receipt.get("snapshot_intent", "")))
    ):
        raise ArchiveBlocked("Managed snapshot access receipt differs")
    capture_managed_import_proof(receipt)


def require_pilot_pin_receipt(data, manifest_sha256):
    """Bind a source pilot to the archive-root receipt before any snapshot I/O."""
    path = Path(os.environ.get("RAW_ARCHIVE_MANAGED_PIN_RECEIPT_FILE", ""))
    expected = os.environ.get("RAW_ARCHIVE_MANAGED_PIN_RECEIPT_SHA256", "")
    epoch = os.environ.get("RAW_ARCHIVE_MANAGED_SCOPE_EPOCH", "")
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not re.fullmatch(r"[a-f0-9]{64}", expected)
        or not re.fullmatch(r"[a-f0-9]{32}", epoch)
    ):
        raise ArchiveBlocked("Pinned managed pilot receipt and scope required")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as body:
            info = os.fstat(body.fileno())
            raw = body.read(1024 * 1024 + 1)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or info.st_mode & 0o077
            or len(raw) > 1024 * 1024
            or hashlib.sha256(raw).hexdigest() != expected
        ):
            raise ArchiveBlocked("Managed pilot receipt differs")
        receipt = json.loads(raw)
    except (OSError, ValueError):
        raise ArchiveBlocked("Managed pilot receipt unavailable") from None
    snapshot = data.get("snapshot", {}) if type(data) is dict else {}
    if (
        type(receipt) is not dict
        or receipt.get("schema") != 1
        or receipt.get("state") != "active"
        or receipt.get("kind") != "raw_pilot"
        or receipt.get("consumer_entrypoint") != "raw-pilot"
        or receipt.get("scope_epoch") != epoch
        or receipt.get("input_sha256") != manifest_sha256
        or receipt.get("snapshot_id") != snapshot.get("provider_id")
        or receipt.get("snapshot_name") != snapshot.get("name")
        or receipt.get("owner") != pilot_process_owner()
        or not re.fullmatch(r"[a-f0-9]{32}", str(receipt.get("pin_id", "")))
        or not re.fullmatch(r"[a-f0-9]{32}", str(receipt.get("snapshot_intent", "")))
    ):
        raise ArchiveBlocked("Managed pilot receipt does not bind this snapshot and manifest")
    return receipt["pin_id"]


def managed_catalog_labels(reference):
    if type(reference) is not dict or set(reference) != {"key", "sha256", "bytes"}:
        raise ArchiveBlocked("Managed snapshot recovery catalogue reference required")
    sha, size = reference["sha256"], reference["bytes"]
    match = re.fullmatch(
        r"(production|staging)/tiered/00000000-0000-0000-0000-000000000000/"
        r"archive_catalog/([0-9]{4}-[0-9]{2}-[0-9]{2})/([a-f0-9]{64})\.json",
        reference["key"] if type(reference["key"]) is str else "",
    )
    if (
        not match
        or type(sha) is not str
        or sha != match[3]
        or type(size) is not int
        or not 0 < size <= 1024 * 1024
    ):
        raise ArchiveBlocked("Invalid managed snapshot recovery catalogue reference")
    return {
        "catalog-env": match[1],
        "catalog-day": match[2],
        "catalog-a": sha[:32],
        "catalog-b": sha[32:],
        "catalog-size": str(size),
    }


def managed_catalog_reference(snapshot):
    labels = snapshot.get("labels", {}) if type(snapshot) is dict else {}
    if type(labels) is not dict:
        raise ArchiveBlocked("Managed snapshot recovery labels are missing")
    sha = labels.get("catalog-a", "") + labels.get("catalog-b", "")
    try:
        size = int(labels.get("catalog-size", ""))
    except (TypeError, ValueError):
        raise ArchiveBlocked("Managed snapshot recovery size is invalid") from None
    reference = {
        "key": f"{labels.get('catalog-env')}/tiered/00000000-0000-0000-0000-000000000000/"
        f"archive_catalog/{labels.get('catalog-day')}/{sha}.json",
        "sha256": sha,
        "bytes": size,
    }
    if any(labels.get(key) != value for key, value in managed_catalog_labels(reference).items()):
        raise ArchiveBlocked("Managed snapshot recovery labels differ")
    return reference


def managed_snapshot_plan(snapshots, records, *, box_id, capacity, now):
    """Compute a conservative plan; this function never creates or deletes anything.

    Records must come from a durable, trusted creation and full-readback journal.
    Provider labels alone are not ownership or readback evidence. This helper is
    deliberately not wired to the scheduler until that journal and verifier exist.
    """
    if (
        type(box_id) is not int
        or box_id <= 0
        or type(capacity) is not int
        or capacity < 3
        or type(snapshots) is not list
        or type(records) is not list
        or len(snapshots) > capacity
        or len(records) > 3
        or not isinstance(now, datetime)
        or now.utcoffset() is None
    ):
        raise ArchiveBlocked("Snapshot capacity or inventory is unknown")

    def identity(row):
        if type(row) is not dict or type(row.get("id")) is not int or row["id"] <= 0:
            raise ArchiveBlocked("Snapshot identity is unknown")
        if row.get("storage_box") != box_id or type(row.get("storage_box")) is not int:
            raise ArchiveBlocked("Snapshot belongs to another Storage Box")
        if type(row.get("name")) is not str or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", row["name"]
        ):
            raise ArchiveBlocked("Snapshot name is unknown")
        try:
            created = datetime.fromisoformat(row["created"].replace("Z", "+00:00"))
            if created.utcoffset() is None or created > now:
                raise ValueError
        except (KeyError, AttributeError, TypeError, ValueError):
            raise ArchiveBlocked("Snapshot creation time is unknown") from None
        return row["id"], row["storage_box"], row["name"], created

    provider = {}
    for snapshot in snapshots:
        key = identity(snapshot)
        if key[0] in provider or any(item[2] == key[2] for item in provider.values()):
            raise ArchiveBlocked("Duplicate provider snapshot identity")
        provider[key[0]] = key

    owned = []
    for record in records:
        if type(record) is not dict or set(record) != {"snapshot", "proof"}:
            raise ArchiveBlocked("Managed snapshot journal is incomplete")
        key = identity(record["snapshot"])
        if provider.get(key[0]) != key or any(item[0] == key[0] for item in owned):
            raise ArchiveBlocked("Managed snapshot ownership or provider identity differs")
        proof = record["proof"]
        if (
            type(proof) is not dict
            or set(proof)
            not in (
                {"snapshot_id", "inventory_sha256", "objects", "bytes", "checked_at"},
                {
                    "snapshot_id",
                    "inventory_sha256",
                    "objects",
                    "bytes",
                    "checked_at",
                    "journal_reference",
                },
            )
            or type(proof["snapshot_id"]) is not int
            or proof["snapshot_id"] != key[0]
            or type(proof["inventory_sha256"]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", proof["inventory_sha256"])
            or type(proof["objects"]) is not int
            or proof["objects"] <= 0
            or type(proof["bytes"]) is not int
            or proof["bytes"] <= 0
        ):
            raise ArchiveBlocked("Complete independent snapshot readback evidence required")
        if "journal_reference" in proof:
            managed_catalog_labels(proof["journal_reference"])
        try:
            checked = datetime.fromisoformat(proof["checked_at"].replace("Z", "+00:00"))
            if checked.utcoffset() is None or not key[3] <= checked <= now:
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            raise ArchiveBlocked("Snapshot readback time is invalid") from None
        owned.append(key)

    if len(owned) < 3:
        if capacity - len(snapshots) < 3 - len(owned):
            raise ArchiveBlocked("Three managed snapshot positions are not available")
        return {"action": "create", "snapshot_id": None}
    oldest = min(owned, key=lambda item: (item[3], item[0]))
    return {"action": "rotate", "snapshot_id": oldest[0]}


def private_token(path):
    if not path.is_absolute() or path.is_symlink():
        raise ArchiveBlocked("Explicit private operator API token file required")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as body:
            info = os.fstat(body.fileno())
            if info.st_uid not in {0, os.getuid()} or info.st_mode & 0o077 or info.st_size > 4096:
                raise ArchiveBlocked("Operator token file must be private and bounded")
            token = body.read(4097).decode().strip()
    except (OSError, ValueError) as error:
        raise ArchiveBlocked("Operator token file is unavailable") from error
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise ArchiveBlocked("Invalid operator API token")
    return token


class SnapshotAPI:
    def __init__(self, client, box_id, username, host):
        if type(box_id) is not int or box_id <= 0:
            raise ArchiveBlocked("Explicit Storage Box ID required")
        if not re.fullmatch(r"u[0-9]+", username) or host != username + ".your-storagebox.de":
            raise ArchiveBlocked("Expected primary Storage Box account required")
        self.client, self.box_id, self.username, self.host = client, box_id, username, host

    def request(self, method, suffix, payload=None):
        with self.client.stream(method, API + suffix, json=payload) as response:
            if response.status_code not in {200, 201}:
                raise ArchiveBlocked("Storage Box operator request refused")
            raw = bytearray()
            for block in response.iter_bytes():
                raw.extend(block)
                if len(raw) > 1024**2:
                    raise ArchiveBlocked("Storage Box metadata exceeds its budget")
        return json.loads(raw)

    def validate_box(self):
        box = self.request("GET", f"/storage_boxes/{self.box_id}")["storage_box"]
        if (
            box.get("id") != self.box_id
            or box.get("username") != self.username
            or box.get("server") != self.host
        ):
            raise ArchiveBlocked("Operator token points to another Storage Box")
        if not box.get("access_settings", {}).get("zfs_enabled"):
            raise ArchiveBlocked("Snapshot directory visibility must be enabled by the operator")
        return box

    def inventory(self):
        """Return a complete manual-snapshot inventory and its provider limit."""
        box = self.validate_box()
        limit = box.get("storage_box_type", {}).get("snapshot_limit")
        if type(limit) is not int or not 3 <= limit <= 100:
            raise ArchiveBlocked("Provider manual snapshot capacity is unknown")
        document = self.request("GET", f"/storage_boxes/{self.box_id}/snapshots")
        rows = document.get("snapshots")
        pagination = document.get("meta", {}).get("pagination", {})
        if (
            type(rows) is not list
            or len(rows) > 100
            or type(pagination) is not dict
            or pagination.get("next_page") not in (None, 0)
        ):
            raise ArchiveBlocked("Complete provider snapshot inventory required")
        if any(type(row) is not dict or type(row.get("is_automatic")) is not bool for row in rows):
            raise ArchiveBlocked("Provider snapshot type is unknown")
        manual = [self.read(row.get("id")) for row in rows if not row["is_automatic"]]
        if len(manual) > limit or len({row["id"] for row in manual}) != len(manual):
            raise ArchiveBlocked("Provider snapshot inventory differs from capacity")
        return manual, limit

    def require_idle_actions(self):
        self.validate_box()
        page = 1
        for _ in range(10):
            result = self.request(
                "GET", f"/storage_boxes/{self.box_id}/actions?per_page=100&page={page}"
            )
            actions = result.get("actions")
            pagination = result.get("meta", {}).get("pagination")
            if (
                type(actions) is not list
                or len(actions) > 100
                or type(pagination) is not dict
                or "next_page" not in pagination
            ):
                raise ArchiveBlocked("Complete provider action inventory required")
            for action in actions:
                if type(action) is not dict or action.get("status") not in {
                    "success",
                    "error",
                    "running",
                }:
                    raise ArchiveBlocked("Provider action state is unknown")
                if action["status"] == "running":
                    raise ArchiveBlocked("Provider Storage Box action is running")
            following = pagination["next_page"]
            if following in (None, 0):
                return
            if type(following) is not int or following != page + 1:
                raise ArchiveBlocked("Provider action pagination differs")
            page = following
        raise ArchiveBlocked("Provider action scan exceeds its budget")

    def create_managed(self, marker, journal_reference):
        if not re.fullmatch(r"[a-f0-9]{32}", marker):
            raise ArchiveBlocked("Unique managed snapshot intent required")
        self.validate_box()
        labels = {
            "purpose": "greenmind-managed",
            "intent": marker,
            **managed_catalog_labels(journal_reference),
        }
        return self.request(
            "POST",
            f"/storage_boxes/{self.box_id}/snapshots",
            {
                "description": "greenmind-managed-" + marker,
                "labels": labels,
            },
        )

    def delete_managed(self, snapshot_id, *, ledger, intent):
        if (
            type(snapshot_id) is not int
            or snapshot_id <= 0
            or type(intent) is not str
            or not re.fullmatch(r"[a-f0-9]{32}", intent)
        ):
            raise ArchiveBlocked("Explicit managed snapshot ID required")
        row = ledger.db.execute(
            "SELECT state,snapshot,proof FROM managed_snapshot WHERE intent=?", (intent,)
        ).fetchone()
        if not row or row[0] != "delete_intent" or not row[1] or not row[2]:
            raise ArchiveBlocked("Durable managed snapshot delete intent required")
        recorded = json.loads(row[1])
        proof = json.loads(row[2])
        if recorded.get("id") != snapshot_id or proof.get("snapshot_id") != snapshot_id:
            raise ArchiveBlocked("Managed snapshot journal identity differs")
        if (
            ledger.db.execute(
                "SELECT count(*) FROM managed_snapshot WHERE state='verified' AND proof IS NOT NULL"
            ).fetchone()[0]
            != 2
        ):
            raise ArchiveBlocked("Two verified managed snapshots must remain")
        self.validate_box()
        live = self.read(snapshot_id)
        if (
            any(
                live.get(key) != recorded.get(key)
                for key in ("id", "storage_box", "name", "created")
            )
            or live.get("description") != "greenmind-managed-" + intent
            or live.get("labels", {}).get("purpose") != "greenmind-managed"
            or live.get("labels", {}).get("intent") != intent
        ):
            raise ArchiveBlocked("Managed snapshot ownership differs before deletion")
        # The caller journals ownership and delete intent before this one request.
        with self.client.stream(
            "DELETE", API + f"/storage_boxes/{self.box_id}/snapshots/{snapshot_id}"
        ) as response:
            if response.status_code not in {200, 202, 204}:
                raise ArchiveBlocked("Managed snapshot delete was not acknowledged")
            raw = bytearray()
            for block in response.iter_bytes():
                raw.extend(block)
                if len(raw) > 1024**2:
                    raise ArchiveBlocked("Managed snapshot action exceeds its budget")
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            raise ArchiveBlocked("Managed snapshot deletion action is unknown") from None

    def require_action(self, action_id, snapshot_id, operation):
        if (
            type(snapshot_id) is not int
            or snapshot_id <= 0
            or operation not in {"create", "delete"}
        ):
            raise ArchiveBlocked("Provider snapshot action identity is unknown")
        if action_id is None and operation == "create":
            # No second POST: reconcile only a complete bounded action listing.
            snapshot = self.read(snapshot_id)
            try:
                created = datetime.fromisoformat(snapshot["created"].replace("Z", "+00:00"))
                if created.utcoffset() is None:
                    raise ValueError
            except (KeyError, AttributeError, TypeError, ValueError):
                raise ArchiveBlocked("Provider snapshot time is unknown") from None
            matches = []
            page = 1
            for _ in range(10):
                data = self.request(
                    "GET", f"/storage_boxes/{self.box_id}/actions?per_page=100&page={page}"
                )
                rows = data.get("actions")
                pagination = data.get("meta", {}).get("pagination")
                if (
                    type(rows) is not list
                    or len(rows) > 100
                    or type(pagination) is not dict
                    or "next_page" not in pagination
                ):
                    raise ArchiveBlocked("Complete provider action inventory required")
                for item in rows:
                    if type(item) is not dict or item.get("command") != "create_snapshot":
                        continue
                    resources = item.get("resources")
                    if type(resources) is not list or not any(
                        type(r) is dict
                        and r.get("type") in {"snapshot", "storage_box_snapshot"}
                        and r.get("id") == snapshot_id
                        for r in resources
                    ):
                        continue
                    try:
                        started = datetime.fromisoformat(item["started"].replace("Z", "+00:00"))
                        if (
                            started.utcoffset() is None
                            or abs((started - created).total_seconds()) > 600
                        ):
                            raise ValueError
                    except (KeyError, AttributeError, TypeError, ValueError):
                        raise ArchiveBlocked("Provider snapshot action time differs") from None
                    matches.append(item)
                following = pagination["next_page"]
                if following in (None, 0):
                    break
                if type(following) is not int or following != page + 1:
                    raise ArchiveBlocked("Provider action pagination differs")
                page = following
            else:
                raise ArchiveBlocked("Provider action scan exceeds its budget")
            if len(matches) != 1 or matches[0].get("status") != "success":
                raise ArchiveBlocked("Snapshot creation action cannot be uniquely reconciled")
            action_id = matches[0].get("id")
        if type(action_id) is not int or action_id <= 0:
            raise ArchiveBlocked("Provider snapshot action identity is unknown")
        action = self.request("GET", f"/storage_boxes/actions/{action_id}").get("action")
        if type(action) is not dict or action.get("id") != action_id:
            raise ArchiveBlocked("Provider snapshot action identity differs")
        resources = action.get("resources")
        if type(resources) is not list:
            raise ArchiveBlocked("Provider snapshot action resources are unknown")
        if not any(
            r.get("type") == "storage_box" and r.get("id") == self.box_id
            for r in resources
            if type(r) is dict
        ):
            raise ArchiveBlocked("Provider snapshot action Storage Box differs")
        if not any(
            r.get("type") in {"snapshot", "storage_box_snapshot"} and r.get("id") == snapshot_id
            for r in resources
            if type(r) is dict
        ):
            raise ArchiveBlocked("Provider snapshot action resource differs")
        if action.get("status") != "success":
            raise ArchiveBlocked("Provider snapshot action has not succeeded")
        if action.get("command") != operation + "_snapshot":
            raise ArchiveBlocked("Provider snapshot action command differs")
        try:
            started = datetime.fromisoformat(action["started"].replace("Z", "+00:00"))
            finished = datetime.fromisoformat(action["finished"].replace("Z", "+00:00"))
            if started.utcoffset() is None or finished.utcoffset() is None or finished < started:
                raise ValueError
        except (KeyError, AttributeError, TypeError, ValueError):
            raise ArchiveBlocked("Provider snapshot action completion time is unknown") from None
        return action_id

    def create(self, description):
        self.validate_box()
        # Exactly one POST. Unknown network outcome is not retried automatically.
        return self.request(
            "POST",
            f"/storage_boxes/{self.box_id}/snapshots",
            {"description": description, "labels": {"purpose": "greenmind-delete-preflight"}},
        )

    def read(self, snapshot_id):
        if type(snapshot_id) is not int or snapshot_id <= 0:
            raise ArchiveBlocked("Explicit snapshot ID required")
        self.validate_box()
        snapshot = self.request("GET", f"/storage_boxes/{self.box_id}/snapshots/{snapshot_id}")[
            "snapshot"
        ]
        if snapshot.get("storage_box") != self.box_id or snapshot.get("id") != snapshot_id:
            raise ArchiveBlocked("Snapshot identity mismatch")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", snapshot.get("name", "")):
            raise ArchiveBlocked("Invalid snapshot name")
        return snapshot


def readback(destination, snapshot, reference, output):
    """Independent SFTP full catalog-manifest read; API success alone proves nothing."""
    private_directory(output)
    path = output / "snapshot-catalog-manifest.json"
    destination.download_catalog(
        reference["key"], path, reference["size"], snapshot=snapshot["name"]
    )
    if path.stat().st_size != reference["size"] or checksum(path) != reference["sha256"]:
        raise ArchiveBlocked("Snapshot catalog readback mismatch")
    return {
        "snapshot": snapshot,
        "catalog_manifest": reference,
        "readback_verified": True,
        "deleted_files": 0,
        "full_restore_required": True,
    }


def main():
    import httpx

    from .runner import destination_from_environment, health_probe

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--box-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true")
    mode.add_argument("--snapshot-id", type=int)
    parser.add_argument("--catalog-reference", type=Path)
    args = parser.parse_args()
    probe = health_probe()
    try:
        if not probe():
            raise ArchiveBlocked("Operator snapshot check paused on receiver headroom")
        private_directory(args.output)
        with httpx.Client(
            headers={"Authorization": "Bearer " + private_token(args.token_file)},
            timeout=httpx.Timeout(8, connect=3),
            trust_env=False,
        ) as client:
            api = SnapshotAPI(
                client,
                args.box_id,
                os.environ["RAW_ARCHIVE_SFTP_USER"],
                os.environ["RAW_ARCHIVE_SFTP_HOST"],
            )
            if args.create:
                result = {
                    "created_at": datetime.now(UTC).isoformat(),
                    "api_response": api.create("GreenMind reviewed archive acceptance"),
                    "readback_verified": False,
                    "deleted_files": 0,
                }
            else:
                if args.catalog_reference is None:
                    parser.error("Snapshot readback requires --catalog-reference")
                snapshot = api.read(args.snapshot_id)
                result = readback(
                    destination_from_environment(),
                    snapshot,
                    json.loads(args.catalog_reference.read_text()),
                    args.output,
                )
        raw = json.dumps(result, sort_keys=True).encode()
        path = args.output / (hashlib.sha256(raw).hexdigest() + ".json")
        with path.open("xb") as body:
            os.chmod(path, 0o600)
            body.write(raw)
        print(
            json.dumps(
                {
                    "report": path.name,
                    "readback_verified": result["readback_verified"],
                    "deleted_files": 0,
                }
            )
        )
    finally:
        probe.close()


if __name__ == "__main__":
    main()
