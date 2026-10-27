"""Measured baseline, consecutive slow responses, durable pause and reader wiring."""

import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.raw_archive.health import HealthProbe, SafetyPause
from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import latency

URLS = ["http://127.0.0.1:8001/health", "http://127.0.0.1:8002/health"]


@pytest.fixture
def baseline(tmp_path):
    value = {
        "schema": 1,
        "environment": "staging",
        "statistic": "p95-nearest-rank",
        "endpoints": [
            dict(url=url, baseline_ms=ms, samples=30)
            for url, ms in zip(URLS, [10, 600], strict=True)
        ],
    }
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    (tmp_path / "control").mkdir()
    return path


def guard(path, role="copy"):
    return latency.LatencyGuard(
        path,
        hashlib.sha256(path.read_bytes()).hexdigest(),
        URLS,
        path.parent / role,
        path.parent / "control",
        role,
    )


def test_three_samples_survive_recreating_probe_and_pause_followup_job(baseline):
    from app.tiered_archive.execution import bounded_job

    assert guard(baseline).thresholds == [110, 720]
    assert guard(baseline).observe([111, 700])["consecutive"] == [1, 0]
    assert guard(baseline).observe([111, 700])["consecutive"] == [2, 0]
    with pytest.raises(SafetyPause, match="receiver_latency"):
        guard(baseline).observe([111, 700])
    assert json.loads((baseline.parent / "control/PAUSE").read_text())["reasons"] == [
        "receiver_latency"
    ]
    with pytest.raises(SafetyPause, match="manual_pause"):
        with bounded_job(baseline.parent):
            pytest.fail("Paused job must not start")


def test_threshold_is_strict_and_each_endpoint_has_independent_streak(baseline):
    probe = guard(baseline)
    assert probe.observe([111, 700])["consecutive"] == [1, 0]
    assert probe.observe([110, 721])["consecutive"] == [0, 1]
    assert probe.observe([111, 721])["consecutive"] == [1, 2]
    with pytest.raises(SafetyPause):
        probe.observe([110, 721])
    value = json.loads(probe.path.read_text())
    assert value["consecutive"] == [0, 3]
    assert "http" not in probe.path.read_text()


def test_pause_is_present_before_third_sample_state_is_committed(baseline, monkeypatch):
    probe = guard(baseline)
    probe.observe([111, 700])
    probe.observe([111, 700])

    def crash(path, value):
        assert (baseline.parent / "control/PAUSE").exists()
        raise OSError("synthetic write failure")

    monkeypatch.setattr(latency, "write_json", crash)
    with pytest.raises(SafetyPause):
        probe.observe([111, 700])
    assert (baseline.parent / "control/PAUSE").exists()


@pytest.mark.parametrize("value", [[-1, 0], [float("nan"), 0], [float("inf"), 0], [1], [True, 0]])
def test_invalid_measurement_latches_pause(baseline, value):
    with pytest.raises(SafetyPause, match="latency_sample_invalid"):
        guard(baseline).observe(value)
    assert (baseline.parent / "control/PAUSE").exists()


@pytest.mark.parametrize("raw", ["[]", "{}", "not json", '{"schema":1,"consecutive":[0]}'])
def test_corrupt_saved_counts_cannot_silently_reset_guard(baseline, raw):
    probe = guard(baseline)
    probe.path.write_text(raw)
    with pytest.raises(SafetyPause, match="latency_state_invalid"):
        guard(baseline).observe([1, 1])


def test_changed_baseline_or_wrong_urls_are_rejected(baseline):
    with pytest.raises(ArchiveBlocked, match="changed"):
        latency.LatencyGuard(
            baseline, "a" * 64, URLS, baseline.parent / "copy", baseline.parent / "control", "copy"
        )
    with pytest.raises(ArchiveBlocked, match="different health endpoints"):
        latency.LatencyGuard(
            baseline,
            hashlib.sha256(baseline.read_bytes()).hexdigest(),
            list(reversed(URLS)),
            baseline.parent / "copy",
            baseline.parent / "control",
            "copy",
        )


@pytest.mark.parametrize(
    "change",
    [
        dict(samples=29),
        dict(baseline_ms=0),
        dict(baseline_ms=1001),
        dict(baseline_ms=True),
        dict(baseline_ms=float("nan")),
    ],
)
def test_unhealthy_or_insufficient_baseline_is_rejected(baseline, change):
    value = json.loads(baseline.read_text())
    value["endpoints"][0].update(change)
    baseline.write_text(json.dumps(value))
    with pytest.raises(ArchiveBlocked, match="Measured healthy"):
        guard(baseline)


def test_baseline_and_saved_counts_reject_symlinks(baseline):
    linked = baseline.with_name("link.json")
    linked.symlink_to(baseline)
    with pytest.raises(OSError):
        guard(linked)
    probe = guard(baseline)
    probe.path.symlink_to(baseline)
    with pytest.raises(SafetyPause, match="latency_state_invalid"):
        probe.observe([1, 1])


def test_staging_requires_baseline_before_health_connections(tmp_path, monkeypatch):
    monkeypatch.setenv("TIERED_ARCHIVE_ENVIRONMENT", "staging")
    monkeypatch.setenv("TIERED_ARCHIVE_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("TIERED_ARCHIVE_LATENCY_BASELINE", raising=False)
    with pytest.raises(ArchiveBlocked, match="reviewed latency baseline"):
        latency.health_probe("copy")


def test_http_complete_body_timing_is_sampled_once_per_fresh_check(tmp_path, monkeypatch):
    now = [0.0]
    observed = []
    original = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda p, *a, **kw: (
            "MemAvailable: 2097152 kB" if str(p) == "/proc/meminfo" else original(p, *a, **kw)
        ),
    )
    monkeypatch.setattr("app.raw_archive.health.os.getloadavg", lambda: (0, 0, 0))
    monkeypatch.setattr(
        "app.raw_archive.health.shutil.disk_usage",
        lambda _: SimpleNamespace(total=10 * 1024**3, free=5 * 1024**3),
    )

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            now[0] += 0.04
            yield b'{"healthy":true}'

    def response(request):
        now[0] += 0.03
        return httpx.Response(200, stream=Body())

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        probe = HealthProbe(
            URLS, [tmp_path], 1024, 1, clock=lambda: now[0], on_sample=observed.append
        )
        probe.client = client
        assert probe()
        assert observed[0] == pytest.approx([70, 70])
        for _ in range(20):
            assert probe()
        assert len(observed) == 1
        now[0] += 5
        assert probe()
        assert len(observed) == 2


@pytest.mark.parametrize("fail_on", [1, 2])
def test_reader_passes_its_bound_health_probe_and_closes_on_pause(tmp_path, monkeypatch, fail_on):
    from app.tiered_archive import read_service

    calls = []
    (tmp_path / "control").mkdir()

    class Probe:
        last = {"reason": "receiver_latency"}

        def __call__(self):
            calls.append("check")
            return calls.count("check") < fail_on

        def close(self):
            calls.append("close")

    class Server:
        def __init__(self, path, config, destination, scratch, *, health_check):
            self.path, self.health = Path(path), health_check

        def __enter__(self):
            self.path.touch()
            return self

        def __exit__(self, *args):
            self.path.unlink()

        def serve_forever(self, **kwargs):
            self.service_actions()

    for name in list(__import__("os").environ):
        if name.startswith("RAW_ARCHIVE_SFTP_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("TIERED_ARCHIVE_READS_ENABLED", "true")
    monkeypatch.setenv("TIERED_ARCHIVE_DELETE_ENABLED", "false")
    monkeypatch.setenv("TIERED_ARCHIVE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TIERED_ARCHIVE_READ_BROKER_SOCKET", str(tmp_path / "test.sock"))
    monkeypatch.setattr(
        read_service, "configuration", lambda: SimpleNamespace(delete_enabled=False)
    )
    monkeypatch.setattr(
        read_service,
        "configured_read_only_box",
        lambda: SimpleNamespace(close=lambda: calls.append("box_close")),
    )
    monkeypatch.setattr(
        read_service,
        "health_probe",
        lambda kind: Probe() if kind == "reader" else pytest.fail("wrong role"),
    )
    monkeypatch.setattr(read_service, "monitored", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(read_service, "Server", Server)
    with pytest.raises(SafetyPause, match="receiver_latency"):
        read_service.main()
    assert calls == ["check"] * fail_on + ["close", "box_close"]
