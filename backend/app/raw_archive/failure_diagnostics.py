"""Bounded static failure origins. Never read exception args, locals or values."""

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from types import CodeType

APP_ROOT = Path(__file__).resolve().parents[1]
MAX_SOURCE_BYTES = 128 * 1024
MAX_TRACEBACK = 64
MAX_FRAMES = 4
MAX_COMPILED_FILES = 4
_SEAL = object()
_CACHE_KEY = "_greenmind_copy_static_failure_v1"
CLASSES = frozenset(
    {
        "ArchiveBlocked",
        "SafetyPause",
        "ImportError",
        "ModuleNotFoundError",
        "RuntimeError",
        "ValueError",
        "TypeError",
        "AttributeError",
        "NameError",
        "KeyError",
        "OSError",
        "PermissionError",
        "FileNotFoundError",
        "TimeoutError",
        "TimeoutExpired",
        "ClientError",
        "RemoteMissing",
        "SnapshotPending",
        "OperationalError",
        "ProgrammingError",
        "IntegrityError",
        "InvalidRequestError",
        "AssertionError",
        "SystemError",
        "MemoryError",
        "KeyboardInterrupt",
        "SystemExit",
    }
)
PAUSE_CODES = frozenset(
    {
        "manual_pause",
        "runtime_budget",
        "service_stop",
        "receiver_headroom",
        "receiver_or_archive_resources",
        "source_memory",
        "source_load",
        "source_receiver_health",
        "source_lease_required",
        "source_lease_timeout",
        "source_lease_lost",
        "source_lease_invalid",
        "source_lease_deadline",
        "source_lease_blocked",
        "host_memory",
        "host_load",
        "host_load_timeout",
        "source_space",
        "host_space",
        "receiver_health",
        "probe_error",
        "memory",
        "receiver_health_body",
        "archive_job_busy",
        "source_lease_unavailable",
        "receiver_latency",
        "latency_state_invalid",
        "latency_sample_invalid",
        "health_probe_error",
        "stage_latency_three_consecutive",
        "staging_latency",
        "gateway_latency",
        "direct_latency",
        "archive_latency",
        "resource_pressure",
    }
)


def exception_class(error):
    name = type(error).__name__
    return name if name in CLASSES else "OtherError"


def failure_reason(error):
    from .health import SafetyPause

    if isinstance(error, SafetyPause):
        value = BaseException.__getattribute__(error, "__dict__").get("code")
        return value if type(value) is str and value in PAUSE_CODES else "unclassified_pause"
    return exception_class(error)


def _codes(code):
    yield code
    for value in code.co_consts:
        if type(value) is CodeType:
            yield from _codes(value)


def _source(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_SOURCE_BYTES:
            return None
        raw = stream.read(MAX_SOURCE_BYTES + 1)
        after = os.fstat(stream.fileno())
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if len(raw) != before.st_size or any(getattr(before, k) != getattr(after, k) for k in fields):
        return None
    return raw


def _frame(tb, sources):
    frame, code = tb.tb_frame, tb.tb_frame.f_code
    name = frame.f_globals.get("__name__")
    module = sys.modules.get(name) if type(name) is str else None
    if module is None or module.__dict__ is not frame.f_globals:
        return None
    spec = module.__spec__
    if spec is None or type(spec.name) is not str or not spec.name.startswith("app."):
        return None
    path = Path(code.co_filename)
    if (
        not path.is_absolute()
        or path.resolve() != path
        or path.suffix != ".py"
        or path != Path(spec.origin)
        or path != Path(module.__file__)
    ):
        return None
    try:
        relative = path.relative_to(APP_ROOT)
    except ValueError:
        return None
    if (
        len(relative.parts) > 8
        or len(relative.as_posix()) > 180
        or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.py)?", p) for p in relative.parts)
        or not 1 <= tb.tb_lineno <= 1000000
        or not re.fullmatch(r"[A-Za-z_<][A-Za-z0-9_.<>]{0,150}", code.co_name)
    ):
        return None
    if (
        next((line for start, end, line in code.co_lines() if start <= tb.tb_lasti < end), None)
        != tb.tb_lineno
    ):
        return None
    if path not in sources:
        if len(sources) >= MAX_COMPILED_FILES:
            return None
        raw = _source(path)
        if raw is None:
            return None
        sources[path] = (
            hashlib.sha256(raw).hexdigest(),
            tuple(
                _codes(
                    compile(raw, str(path), "exec", dont_inherit=True, optimize=sys.flags.optimize)
                )
            ),
        )
    source_sha, compiled = sources[path]
    # Merely forging co_filename or using an actual module's globals is not
    # enough: the executing code must be byte-for-byte compiled from that file.
    if not any(code == expected for expected in compiled):
        return None
    return dict(
        file=relative.as_posix(), function=code.co_name, line=tb.tb_lineno, source_sha256=source_sha
    )


def failure_diagnostic(error):
    """Compute once so entry JSON and copy-last-run inherit identical evidence."""
    kind = exception_class(error)
    fallback = dict(
        version=1,
        guard_code="copy_guard_v1_unavailable",
        exception_class=kind,
        frames=[],
        frames_truncated=False,
    )
    try:
        attributes = BaseException.__getattribute__(error, "__dict__")
        cached = attributes.get(_CACHE_KEY)
        if (
            type(cached) is tuple
            and len(cached) == 2
            and cached[0] is _SEAL
            and type(cached[1]) is bytes
        ):
            return json.loads(cached[1])
        tb, count, trace, frames, sources = error.__traceback__, 0, [], [], {}
        while tb is not None and count < MAX_TRACEBACK:
            trace.append(tb)
            count += 1
            tb = tb.tb_next
        truncated = tb is not None
        # Never label an outer frame as the true origin of an overlong trace.
        if not truncated:
            for candidate in reversed(trace):
                checked = _frame(candidate, sources)
                if checked is not None:
                    frames.append(checked)
                if len(frames) == MAX_FRAMES:
                    break
        value = dict(version=1, exception_class=kind, frames=frames, frames_truncated=truncated)
        pin = hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        value["guard_code"] = "copy_guard_v1_" + pin[:20]
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        if len(raw) > 4096:
            return fallback
        attributes[_CACHE_KEY] = (_SEAL, raw)
        return json.loads(raw)
    except BaseException:
        return fallback
