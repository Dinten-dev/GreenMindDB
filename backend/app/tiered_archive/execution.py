"""Bounded job lifetime and durable local outcome; no external notifications."""

import json
import os
import resource
import signal
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.raw_archive.failure_diagnostics import failure_diagnostic, failure_reason
from app.raw_archive.health import SafetyPause

from .resources import monitored


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".pending")
    with temporary.open("w") as body:
        os.chmod(temporary, 0o600)
        json.dump(value, body, sort_keys=True)
        body.flush()
        os.fsync(body.fileno())
    os.replace(temporary, path)


@contextmanager
def bounded_job(root, *, seconds=300, kind="copy", emit=True):
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (root / "PAUSE").exists() or (root / "control/PAUSE").exists():
        raise SafetyPause("manual_pause")
    began = time.monotonic()
    state = dict(schema=1, kind=kind, state="running", started_at=datetime.now(UTC).isoformat())
    path = root / (kind + "-last-run.json")
    atomic_json(path, state)

    def stop(signum, _frame):
        raise SafetyPause("runtime_budget" if signum == signal.SIGALRM else "service_stop")

    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGALRM, signal.SIGTERM)}
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    if old_timer[0]:
        raise RuntimeError("Nested process deadline is not supported")
    for sig in previous:
        signal.signal(sig, stop)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        with monitored(root, kind=kind):
            yield
        state["state"] = "success"
    except BaseException as error:
        state["state"] = "paused" if isinstance(error, SafetyPause) else "failed"
        state["reason"] = failure_reason(error)
        state["diagnostic"] = failure_diagnostic(error)
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        state.update(
            finished_at=datetime.now(UTC).isoformat(),
            elapsed_seconds=round(time.monotonic() - began, 3),
            self_peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        )
        atomic_json(path, state)
        # cgroup memory.peak and child processes are measured separately by the
        # native staging acceptance. ru_maxrss is deliberately not called that.
        if emit:
            print(json.dumps({"event": "tiered_job_finished", **state}), flush=True)
