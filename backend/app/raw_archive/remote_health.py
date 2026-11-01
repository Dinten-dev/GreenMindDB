"""Separate worker capacity from authenticated, fresh production-source capacity."""

import math
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from .health import SafetyPause
from .policy import ArchiveBlocked


class CombinedProbe:
    def __init__(
        self,
        local,
        *,
        url,
        token,
        source,
        minimum_memory,
        maximum_load,
        cache_seconds=0,
        clock=time.time,
        monotonic=time.monotonic,
    ):
        parts = urlsplit(url)
        if (
            parts.scheme != "http"
            or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parts.path != "/source-health"
            or parts.query
            or parts.fragment
            or parts.username
            or parts.password
        ):
            raise ArchiveBlocked("Remote source probe requires an explicit loopback SSH transport")
        if (
            len(token) < 32
            or not source
            or not math.isfinite(minimum_memory)
            or minimum_memory < 512
            or not math.isfinite(maximum_load)
            # Never exceed the independent production lease's original ceiling.
            or not 0 < maximum_load <= 2.4
        ):
            raise ArchiveBlocked("Authenticated source identity and original reserve required")
        if (
            type(cache_seconds) not in (int, float)
            or not math.isfinite(cache_seconds)
            or not 0 <= cache_seconds <= 1
        ):
            raise ArchiveBlocked("Source health cache must be finite and at most one second")
        self.local, self.url, self.token, self.source = local, url, token, source
        self.minimum_memory, self.maximum_load, self.clock = minimum_memory, maximum_load, clock
        self.monotonic = monotonic
        self.cache_seconds = cache_seconds
        self._source_cache = None
        self.last = {"ok": False, "reason": "not_checked"}

    def close(self):
        self._source_cache = None
        self.local.close()

    def fail(self, reason):
        self._source_cache = None
        self.last = {"ok": False, "reason": reason}
        return False

    def __call__(self):
        try:
            # Local reserves and the caller's source-lease checks remain fresh
            # at every checkpoint. Only a nonce-verified Source GET can coalesce.
            if not self.local():
                self._source_cache = None
                self.last = self.local.last
                return False
            binding = (self.url, self.token, self.source, self.minimum_memory, self.maximum_load)
            if self.cache_seconds > 0 and self._source_cache is not None:
                accepted, source_age, cached_binding, last = self._source_cache
                delta = self.monotonic() - accepted
                if not math.isfinite(delta) or delta < 0:
                    return self.fail("source_probe_invalid")
                if (
                    binding == cached_binding
                    and delta < self.cache_seconds
                    and source_age + delta <= 10
                ):
                    self.last = {
                        **last,
                        "worker_available_mib": self.local.last.get("available_mib"),
                    }
                    return True
                self._source_cache = None
            nonce = secrets.token_hex(16)
            began = self.monotonic()
            if self.cache_seconds > 0 and not math.isfinite(began):
                return self.fail("source_probe_invalid")
            with self.local.client.stream(
                "GET",
                self.url,
                headers={"Authorization": "Bearer " + self.token, "X-GreenMind-Probe-Nonce": nonce},
            ) as response:
                if response.status_code != 200:
                    return self.fail("source_probe_unavailable")
                raw = bytearray()
                for block in response.iter_bytes():
                    raw.extend(block)
                    if len(raw) > 8192:
                        return self.fail("source_probe_invalid")
            import json

            value = json.loads(raw)
            if not isinstance(value, dict):
                return self.fail("source_probe_invalid")
            if value.get("schema") != 1 or value.get("source") != self.source:
                return self.fail("source_identity")
            nonce_bound = "probe_nonce" in value or "sample_age_seconds" in value
            if nonce_bound:
                if value.get("probe_nonce") != nonce:
                    return self.fail("source_probe_invalid")
                sample_age = float(value["sample_age_seconds"])
                ended = self.monotonic()
                elapsed = ended - began
                if not math.isfinite(sample_age) or sample_age < 0 or elapsed < 0:
                    return self.fail("source_probe_invalid")
                age = sample_age + elapsed
            else:
                age = self.clock() - float(value["checked_at"])
            if not math.isfinite(age) or not 0 <= age <= 10:
                return self.fail("source_probe_stale")
            memory, load = float(value["available_mib"]), float(value["load"])
            if not math.isfinite(memory) or not math.isfinite(load) or memory < 0 or load < 0:
                return self.fail("source_probe_invalid")
            if memory < self.minimum_memory:
                return self.fail("source_memory")
            if load > self.maximum_load:
                return self.fail("source_load")
            if value.get("routes") != {"8120": 200, "8003": 200, "8000": 200}:
                return self.fail("source_receiver_health")
            mounts = value.get("mounts")
            if (
                not isinstance(mounts, list)
                or not 1 <= len(mounts) <= 8
                or any(
                    not isinstance(m, dict)
                    or type(m.get("free")) is not int
                    or type(m.get("total")) is not int
                    or not 0 <= m["free"] <= m["total"]
                    or m["free"] < max(2 * 1024**3, m["total"] * 0.05)
                    for m in mounts
                )
            ):
                return self.fail("source_space")
            self.last = {
                "ok": True,
                "reason": None,
                "source_available_mib": value["available_mib"],
                "worker_available_mib": self.local.last.get("available_mib"),
            }
            if self.cache_seconds > 0 and nonce_bound:
                accepted = self.monotonic()
                validation_elapsed = accepted - ended
                if not math.isfinite(validation_elapsed) or validation_elapsed < 0:
                    return self.fail("source_probe_invalid")
                age += validation_elapsed
                if age > 10:
                    return self.fail("source_probe_stale")
                self._source_cache = (accepted, age, binding, dict(self.last))
            return True
        except SafetyPause:
            self._source_cache = None
            raise
        except Exception:
            return self.fail("source_probe_error")


def combined(local):
    url = os.getenv("RAW_ARCHIVE_SOURCE_PROBE_URL")
    if not url:
        if os.getenv("RAW_ARCHIVE_WORKER_ROLE") == "archive":
            raise ArchiveBlocked(
                "Archive-host source operations require authenticated production telemetry"
            )
        return local
    path = Path(os.environ["RAW_ARCHIVE_SOURCE_PROBE_TOKEN_FILE"])
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ArchiveBlocked("Private source-probe token required")
    try:
        cache_seconds = float(os.getenv("RAW_ARCHIVE_SOURCE_PROBE_CACHE_SECONDS", "0"))
    except ValueError:
        raise ArchiveBlocked("Invalid source health cache duration") from None
    return CombinedProbe(
        local,
        url=url,
        token=path.read_text().strip(),
        source=os.environ["RAW_ARCHIVE_SOURCE_ID"],
        minimum_memory=int(os.getenv("RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB", "512")),
        maximum_load=float(os.getenv("RAW_ARCHIVE_SOURCE_MAX_LOAD", "2")),
        cache_seconds=cache_seconds,
    )
