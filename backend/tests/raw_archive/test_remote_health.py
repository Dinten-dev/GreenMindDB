"""Moving work never substitutes the empty worker's memory for source headroom."""

import copy
import json

import httpx
import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.remote_health import CombinedProbe


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
