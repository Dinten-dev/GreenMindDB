"""Shared HTTP client; resource guards stay fresh at every checkpoint."""

import os
import shutil
import time
from pathlib import Path

import httpx

from .policy import ArchiveBlocked
from .telemetry import measure


class SafetyPause(ArchiveBlocked):
    def __init__(self, code, details=None):
        super().__init__("Archive paused: " + code)
        self.code = code
        self.details = details or {}


class HealthProbe:
    def __init__(
        self,
        urls,
        mounts,
        minimum_memory,
        maximum_load,
        *,
        allow_low_source_space=False,
        metrics=None,
        clock=time.monotonic,
    ):
        self.urls, self.mounts = urls, mounts
        self.minimum_memory, self.maximum_load = minimum_memory, maximum_load
        self.allow_low_source_space = allow_low_source_space
        self.metrics, self.clock = metrics, clock
        self.last_http = float("-inf")
        self.last = {"ok": False, "reason": "not_checked"}
        self._client_context = None
        self.client = None

    def close(self):
        if self._client_context is not None:
            self._client_context.__exit__(None, None, None)
            self._client_context = self.client = None

    def fail(self, reason, **values):
        self.last = {"ok": False, "reason": reason, **values}
        if self.metrics is not None:
            self.metrics.counts["guard_" + reason] += 1
        return False

    def __call__(self):
        try:
            available = (
                next(
                    int(line.split()[1])
                    for line in Path("/proc/meminfo").read_text().splitlines()
                    if line.startswith("MemAvailable:")
                )
                / 1024
            )
            load = os.getloadavg()[0]
            if available < self.minimum_memory:
                return self.fail(
                    "memory", available_mib=round(available, 1), required_mib=self.minimum_memory
                )
            for index, mount in enumerate(self.mounts):
                usage = shutil.disk_usage(mount)
                required = max(2 * 1024**3, usage.total * 0.05)
                if not self.allow_low_source_space and usage.free < required:
                    return self.fail(
                        "source_space",
                        mount_index=index,
                        free_bytes=usage.free,
                        required_bytes=int(required),
                    )
            if self.clock() - self.last_http >= 5:
                if self.client is None:
                    self._client_context = httpx.Client(
                        timeout=3, follow_redirects=False, trust_env=False
                    )
                    self.client = self._client_context.__enter__()
                with measure(self.metrics, "receiver_health"):
                    for index, url in enumerate(self.urls):
                        with self.client.stream("GET", url) as response:
                            if response.status_code != 200:
                                return self.fail(
                                    "receiver_health",
                                    endpoint_index=index,
                                    status=response.status_code,
                                )
                # Cache only successful checks, for at most five seconds.
                self.last_http = self.clock()
            if load > self.maximum_load:
                return self.fail("host_load", load=round(load, 3), maximum=self.maximum_load)
            self.last = {
                "ok": True,
                "reason": None,
                "available_mib": round(available, 1),
                "load": round(load, 3),
                "maximum_load": self.maximum_load,
            }
            return True
        except (OSError, ValueError, StopIteration, httpx.HTTPError):
            return self.fail("probe_error")


def await_headroom(
    healthy, *, deadline, paused, metrics=None, clock=time.monotonic, sleep=time.sleep
):
    """Wait only for transient load. All other failures release resources immediately."""
    end = min(deadline, clock() + 120)
    consecutive = 0
    while True:
        if paused():
            raise SafetyPause("manual_pause")
        if clock() >= deadline:
            raise SafetyPause("runtime_budget")
        ok = healthy()
        state = getattr(healthy, "last", {})
        reason = state.get("reason", "receiver_headroom")
        if ok:
            consecutive += 1
            if consecutive == 3:
                return
        elif reason != "host_load":
            raise SafetyPause(reason, state)
        else:
            consecutive = 0
        if clock() >= end:
            raise SafetyPause("host_load_timeout", state)
        with measure(metrics, "headroom_wait"):
            sleep(min(10, max(0, end - clock())))
