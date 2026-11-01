"""Moving work never substitutes the empty worker's memory for source headroom."""

import copy
import json
import signal

import httpx
import pytest

from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.remote_health import CombinedProbe, combined
from app.tiered_archive.execution import bounded_job


class Local:
    last = {"ok": True, "available_mib": 3000}

    def __init__(self, body):
        self.client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=json.dumps(body).encode())
            )
        )

    def __call__(self):
        return self.last["ok"]

    def close(self):
        self.client.close()


GOOD = {
    "schema": 1,
    "checked_at": 1000,
    "source": "production-main",
    "available_mib": 700,
    "load": 1,
    "routes": {"8120": 200, "8003": 200, "8000": 200},
    "mounts": [{"total": 100 * 1024**3, "free": 20 * 1024**3}],
}


def probe(body, **values):
    return CombinedProbe(
        Local(body),
        url="http://127.0.0.1:18150/source-health",
        token="a" * 64,
        source="production-main",
        minimum_memory=512,
        maximum_load=2,
        clock=lambda: 1001,
        **values,
    )


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("available_mib", 511, "source_memory"),
        ("load", 2.1, "source_load"),
        ("checked_at", 989, "source_probe_stale"),
        ("checked_at", 1002, "source_probe_stale"),
        ("source", "another-server", "source_identity"),
        ("schema", 2, "source_identity"),
        ("available_mib", float("nan"), "source_probe_invalid"),
        ("load", float("inf"), "source_probe_invalid"),
        ("routes", {"8120": 200, "8003": 503, "8000": 200}, "source_receiver_health"),
        ("mounts", [], "source_space"),
        ("mounts", [{"total": 100 * 1024**3, "free": 1}], "source_space"),
    ],
)
def test_source_failure_never_bypassed_by_worker_capacity(field, value, reason):
    body = copy.deepcopy(GOOD)
    body[field] = value
    healthy = probe(body)
    try:
        assert not healthy()
        assert healthy.last["reason"] == reason
    finally:
        healthy.close()


def test_authenticated_fresh_source_and_worker_both_required():
    healthy = probe(GOOD)
    try:
        assert healthy()
        healthy.local.last = {"ok": False, "reason": "memory"}
        assert not healthy()
        assert healthy.last["reason"] == "memory"
    finally:
        healthy.close()


def test_public_probe_and_weakened_source_reserve_rejected():
    with pytest.raises(ArchiveBlocked):
        CombinedProbe(
            Local(GOOD),
            url="http://2.31.22.33:8150/source-health",
            token="a" * 64,
            source="production-main",
            minimum_memory=512,
            maximum_load=2,
        )

    with pytest.raises(ArchiveBlocked):
        CombinedProbe(
            Local(GOOD),
            url="http://127.0.0.1:8150/source-health",
            token="a" * 64,
            source="production-main",
            minimum_memory=128,
            maximum_load=2,
        )


@pytest.mark.parametrize("limit", [2.4, 2.401])
def test_original_production_lease_ceiling_remains_hard(limit):
    local = Local({**GOOD, "load": 2.3})
    try:
        if limit > 2.4:
            with pytest.raises(ArchiveBlocked):
                CombinedProbe(
                    local,
                    url="http://127.0.0.1:18150/source-health",
                    token="a" * 64,
                    source="production-main",
                    minimum_memory=640,
                    maximum_load=limit,
                    clock=lambda: 1001,
                )
        else:
            healthy = CombinedProbe(
                local,
                url="http://127.0.0.1:18150/source-health",
                token="a" * 64,
                source="production-main",
                minimum_memory=640,
                maximum_load=limit,
                clock=lambda: 1001,
            )
            assert healthy()
    finally:
        local.close()


@pytest.mark.parametrize(
    "age,elapsed,nonce_ok,expected",
    [
        (2, 1, True, True),
        (0, 11, True, False),
        (9.5, 1, True, False),
        (-1, 0, True, False),
        (0, 0, False, False),
        (float("nan"), 0, True, False),
    ],
)
def test_nonce_bound_monotonic_freshness_ignores_clock_skew(age, elapsed, nonce_ok, expected):
    local = Local(GOOD)
    local.client.close()

    def reply(request):
        return httpx.Response(
            200,
            json={
                **GOOD,
                "checked_at": 9999999,
                "probe_nonce": request.headers["X-GreenMind-Probe-Nonce"] if nonce_ok else "0" * 32,
                "sample_age_seconds": age,
            },
        )

    local.client = httpx.Client(transport=httpx.MockTransport(reply))
    ticks = iter([10, 10 + elapsed])
    healthy = CombinedProbe(
        local,
        url="http://127.0.0.1:18150/source-health",
        token="a" * 64,
        source="production-main",
        minimum_memory=640,
        maximum_load=2,
        clock=lambda: 0,
        monotonic=lambda: next(ticks),
    )
    try:
        assert healthy() is expected
    finally:
        healthy.close()


@pytest.mark.parametrize(
    "sig,reason", [(signal.SIGALRM, "runtime_budget"), (signal.SIGTERM, "service_stop")]
)
def test_signal_stop_during_http_remains_a_durable_safety_pause(tmp_path, monkeypatch, sig, reason):
    monkeypatch.setenv("TIERED_ARCHIVE_CGROUP_MONITOR", "false")
    healthy = probe(GOOD)
    healthy.local.client.close()

    def interrupted(request):
        signal.raise_signal(sig)
        raise AssertionError("Safety stop must interrupt the HTTP operation")

    healthy.local.client = httpx.Client(transport=httpx.MockTransport(interrupted))
    try:
        with pytest.raises(SafetyPause) as caught:
            with bounded_job(tmp_path, seconds=10, kind="health-stop", emit=False):
                healthy()
        assert caught.value.code == reason
        state = json.loads((tmp_path / "health-stop-last-run.json").read_text())
        assert state["state"] == "paused"
        assert state["reason"] == reason
    finally:
        healthy.close()


class CacheClock:
    now = 0.0

    def __call__(self):
        return self.now


class CacheLocal(Local):
    def __init__(self, clock):
        self.clock = clock
        self.calls = self.requests = 0
        self.last = {"ok": True, "available_mib": 3000}
        self.status = 200
        self.age = self.latency = 0
        self.fields = {}
        self.error = self.local_error = None
        self.client = httpx.Client(transport=httpx.MockTransport(self.reply))

    def reply(self, request):
        self.requests += 1
        self.clock.now += self.latency
        if self.error is not None:
            raise self.error
        return httpx.Response(
            self.status,
            json={
                **GOOD,
                "probe_nonce": request.headers["X-GreenMind-Probe-Nonce"],
                "sample_age_seconds": self.age,
                **self.fields,
            },
        )

    def __call__(self):
        self.calls += 1
        if self.local_error is not None:
            raise self.local_error
        return self.last["ok"]


def cached_probe():
    clock = CacheClock()
    local = CacheLocal(clock)
    healthy = CombinedProbe(
        local,
        url="http://127.0.0.1:18150/source-health",
        token="a" * 64,
        source="production-main",
        minimum_memory=640,
        maximum_load=2,
        monotonic=clock,
        cache_seconds=1,
    )
    return healthy, local, clock


def test_cache_coalesces_only_source_get_and_keeps_every_local_and_lease_check():
    from app.raw_archive.remote_lease import bound_source_lease, source_checkpoint

    healthy, local, clock = cached_probe()
    leases = []

    def checkpoint():
        assert healthy()
        leases.append(True)

    try:
        with bound_source_lease(checkpoint, copy_only=True):
            for index in range(10):
                clock.now = index / 10
                local.last["available_mib"] = 2000 + index
                source_checkpoint()
                assert healthy.last["worker_available_mib"] == 2000 + index
        assert local.calls == len(leases) == 11
        assert local.requests == 1
        clock.now = 1
        assert healthy()
        assert local.requests == 2
    finally:
        healthy.close()


def test_cache_never_extends_nonce_sample_age_past_ten_seconds():
    healthy, local, clock = cached_probe()
    try:
        local.age, local.latency = 9.5, 0.2
        assert healthy()
        clock.now = 0.6  # 9.5 + .2 request + .4 cache age exceeds ten.
        assert healthy()
        assert local.requests == 2
    finally:
        healthy.close()


@pytest.mark.parametrize("now", [-1, float("nan"), float("inf")])
def test_invalid_cache_monotonic_delta_invalidates_previous_success(now):
    healthy, local, clock = cached_probe()
    try:
        assert healthy()
        clock.now = now
        assert not healthy()
        assert healthy.last["reason"] == "source_probe_invalid"
        assert healthy._source_cache is None and local.requests == 1
        clock.now = 0.1
        assert healthy()
        assert local.requests == 2
    finally:
        healthy.close()


def test_local_failure_invalidates_cache_before_recovery():
    healthy, local, clock = cached_probe()
    try:
        assert healthy()
        local.last = {"ok": False, "reason": "memory"}
        clock.now = 0.1
        assert not healthy()
        assert healthy.last["reason"] == "memory" and healthy._source_cache is None
        local.last = {"ok": True, "available_mib": 3000}
        assert healthy()
        assert local.requests == 2
    finally:
        healthy.close()


@pytest.mark.parametrize(
    "fields,status,error,reason",
    [
        ({}, 503, None, "source_probe_unavailable"),
        ({"probe_nonce": "0" * 32}, 200, None, "source_probe_invalid"),
        ({"source": "foreign"}, 200, None, "source_identity"),
        ({"sample_age_seconds": 11}, 200, None, "source_probe_stale"),
        ({"routes": {}}, 200, None, "source_receiver_health"),
        ({"mounts": []}, 200, None, "source_space"),
        ({"available_mib": 1}, 200, None, "source_memory"),
        ({"load": 3}, 200, None, "source_load"),
        ({}, 200, httpx.ReadTimeout("synthetic"), "source_probe_error"),
    ],
)
def test_failed_source_recheck_cannot_reuse_or_cache_success(fields, status, error, reason):
    healthy, local, clock = cached_probe()
    try:
        assert healthy()
        clock.now = 1
        local.fields, local.status, local.error = fields, status, error
        assert not healthy()
        assert healthy.last["reason"] == reason and healthy._source_cache is None
        local.fields, local.status, local.error = {}, 200, None
        clock.now = 1.1
        assert healthy()
        assert local.requests == 3
    finally:
        healthy.close()


@pytest.mark.parametrize("where", ["local", "http"])
def test_safety_pause_invalidates_cache_and_preserves_exception(where):
    healthy, local, clock = cached_probe()
    pause = SafetyPause("runtime_budget")
    try:
        assert healthy()
        if where == "local":
            local.local_error = pause
        else:
            clock.now, local.error = 1, pause
        with pytest.raises(SafetyPause) as caught:
            healthy()
        assert caught.value is pause and healthy._source_cache is None
        local.local_error = local.error = None
        assert healthy()
        assert local.requests == (2 if where == "local" else 3)
    finally:
        healthy.close()


@pytest.mark.parametrize(
    "sig,reason", [(signal.SIGALRM, "runtime_budget"), (signal.SIGTERM, "service_stop")]
)
def test_real_signal_with_populated_cache_is_not_swallowed(tmp_path, monkeypatch, sig, reason):
    monkeypatch.setenv("TIERED_ARCHIVE_CGROUP_MONITOR", "false")
    healthy, local, clock = cached_probe()
    try:
        assert healthy()
        clock.now = 1

        def interrupted(request):
            signal.raise_signal(sig)
            raise AssertionError("Safety stop must interrupt the operation")

        local.client.close()
        local.client = httpx.Client(transport=httpx.MockTransport(interrupted))
        with pytest.raises(SafetyPause) as caught:
            with bounded_job(tmp_path, seconds=10, kind="cached-stop", emit=False):
                healthy()
        assert caught.value.code == reason and healthy._source_cache is None
        state = json.loads((tmp_path / "cached-stop-last-run.json").read_text())
        assert state["state"] == "paused" and state["reason"] == reason
    finally:
        healthy.close()


@pytest.mark.parametrize(
    "field,value",
    [("token", "b" * 64), ("source", "foreign"), ("url", "http://127.0.0.1:18151/source-health")],
)
def test_cache_is_bound_to_instance_and_authentication_target(field, value):
    healthy, local, _ = cached_probe()
    other, other_local, _ = cached_probe()
    try:
        assert healthy() and other()
        assert local.requests == other_local.requests == 1
        setattr(healthy, field, value)
        healthy()
        assert local.requests == 2
    finally:
        healthy.close()
        other.close()


def test_close_invalidates_source_cache():
    healthy, _, _ = cached_probe()
    assert healthy()
    healthy.close()
    assert healthy._source_cache is None
    assert not healthy()


def test_legacy_wall_clock_only_response_is_never_cached():
    healthy = probe(GOOD, cache_seconds=1)
    try:
        assert healthy() and healthy()
        assert healthy._source_cache is None
    finally:
        healthy.close()


@pytest.mark.parametrize("duration", [-0.1, 1.001, float("nan"), float("inf"), True, "1"])
def test_source_cache_duration_is_strictly_bounded(duration):
    local = Local(GOOD)
    try:
        with pytest.raises(ArchiveBlocked):
            CombinedProbe(
                local,
                url="http://127.0.0.1:18150/source-health",
                token="a" * 64,
                source="production-main",
                minimum_memory=640,
                maximum_load=2,
                cache_seconds=duration,
            )
    finally:
        local.close()


@pytest.mark.parametrize("configured,expected", [(None, 0), ("0", 0), (".5", 0.5), ("1", 1)])
def test_factory_source_cache_is_opt_in(monkeypatch, tmp_path, configured, expected):
    token = tmp_path / "probe-token"
    token.write_text("a" * 64)
    token.chmod(0o600)
    monkeypatch.setenv("RAW_ARCHIVE_SOURCE_PROBE_URL", "http://127.0.0.1:18150/source-health")
    monkeypatch.setenv("RAW_ARCHIVE_SOURCE_PROBE_TOKEN_FILE", str(token))
    monkeypatch.setenv("RAW_ARCHIVE_SOURCE_ID", "production-main")
    monkeypatch.delenv("RAW_ARCHIVE_SOURCE_PROBE_CACHE_SECONDS", raising=False)
    if configured is not None:
        monkeypatch.setenv("RAW_ARCHIVE_SOURCE_PROBE_CACHE_SECONDS", configured)
    healthy = combined(Local(GOOD))
    try:
        assert healthy.cache_seconds == expected
    finally:
        healthy.close()


@pytest.mark.parametrize("configured", ["-1", "1.01", "nan", "inf", "invalid"])
def test_factory_rejects_invalid_cache_duration(monkeypatch, tmp_path, configured):
    token = tmp_path / "probe-token"
    token.write_text("a" * 64)
    token.chmod(0o600)
    monkeypatch.setenv("RAW_ARCHIVE_SOURCE_PROBE_URL", "http://127.0.0.1:18150/source-health")
    monkeypatch.setenv("RAW_ARCHIVE_SOURCE_PROBE_TOKEN_FILE", str(token))
    monkeypatch.setenv("RAW_ARCHIVE_SOURCE_ID", "production-main")
    monkeypatch.setenv("RAW_ARCHIVE_SOURCE_PROBE_CACHE_SECONDS", configured)
    local = Local(GOOD)
    try:
        with pytest.raises(ArchiveBlocked):
            combined(local)
    finally:
        local.close()
