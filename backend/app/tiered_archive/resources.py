"""Sample cgroup v2 while work is alive; page-cache peaks are observational."""

import json
import os
import signal
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ANON_LIMIT = 128 * 1024**2


def read_cgroup(proc=Path("/proc/self/cgroup"), mount=Path("/sys/fs/cgroup")):
    try:
        group = next(line[3:] for line in proc.read_text().splitlines() if line.startswith("0::"))
        if not group.startswith("/") or ".." in Path(group).parts:
            raise ValueError("Invalid cgroup")
        directory = mount / group.lstrip("/")
        events = {
            k: int(v)
            for k, v in (
                line.split() for line in (directory / "memory.events").read_text().splitlines()
            )
        }
        stats = {
            k: int(v)
            for k, v in (
                line.split() for line in (directory / "memory.stat").read_text().splitlines()
            )
        }
        if (
            any(name not in events for name in ("high", "max", "oom", "oom_kill"))
            or "anon" not in stats
        ):
            raise ValueError("Missing memory counters")
        try:
            peak = int((directory / "memory.peak").read_text())
        except OSError:
            peak = None
        return {
            "available": True,
            "events": events,
            "anon_bytes": stats["anon"],
            "file_bytes": stats.get("file"),
            "peak_bytes": peak,
        }
    except (OSError, ValueError, StopIteration):
        return {"available": False}


def reasons(sample):
    if not sample.get("available"):
        return ["cgroup_metrics_unavailable"]
    result = [
        "memory_event_" + event
        for event in ("max", "oom", "oom_kill")
        if sample["events"][event] > 0
    ]
    if sample["anon_bytes"] > ANON_LIMIT:
        result.append("anonymous_memory_limit")
    return result


def write_json(path, value):
    temporary = path.with_suffix(".pending")
    with temporary.open("w") as out:
        os.chmod(temporary, 0o600)
        json.dump(value, out, sort_keys=True)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)


def pause(directory, kind, causes):
    try:
        fd = os.open(directory / "PAUSE", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    except FileExistsError:
        return
    with os.fdopen(fd, "w") as out:
        os.fchmod(out.fileno(), 0o640)
        out.write(json.dumps({"at": time.time(), "kind": kind, "reasons": causes}) + "\n")
        out.flush()
        os.fsync(out.fileno())


class Sampler:
    def __init__(self, root, kind, pause_dir, *, read=read_cgroup, stop=None):
        self.path = Path(root) / (kind + "-resources.json")
        self.kind, self.pause_dir, self.read = kind, Path(pause_dir), read
        self.stop = stop or (lambda: os.kill(os.getpid(), signal.SIGTERM))
        self.done = threading.Event()
        self.failed = False
        self.value = dict(
            schema=1,
            invocation_id=os.environ.get("INVOCATION_ID"),
            samples=0,
            sampled_anon_peak_bytes=0,
            sample_failures=0,
            completed=False,
            reasons=[],
        )

    def sample(self):
        current = self.read()
        self.value.update(last_at=time.time(), last=current)
        self.value["samples"] += 1
        if current.get("available"):
            self.value["sampled_anon_peak_bytes"] = max(
                self.value["sampled_anon_peak_bytes"], current["anon_bytes"]
            )
        else:
            self.value["sample_failures"] += 1
        causes = reasons(current)
        self.value["reasons"] = sorted(set(self.value["reasons"]) | set(causes))
        write_json(self.path, self.value)
        if causes and not self.failed:
            self.failed = True
            pause(self.pause_dir, self.kind, causes)
            self.stop()

    def run(self):
        while not self.done.wait(0.5):
            try:
                self.sample()
            except Exception:
                if not self.failed:
                    self.failed = True
                    try:
                        pause(self.pause_dir, self.kind, ["resource_sampling_failed"])
                    finally:
                        self.stop()
                return


@contextmanager
def monitored(root, *, kind, pause_dir=None):
    if os.environ.get("TIERED_ARCHIVE_CGROUP_MONITOR") != "true":
        yield
        return
    root = Path(root)
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    sampler = Sampler(root, kind, pause_dir or root / "control")
    sampler.sample()
    thread = threading.Thread(target=sampler.run, name="archive-memory-monitor", daemon=True)
    thread.start()
    try:
        yield
    finally:
        sampler.done.set()
        thread.join(timeout=2)
        sampler.sample()
        sampler.value["completed"] = not thread.is_alive()
        write_json(sampler.path, sampler.value)
