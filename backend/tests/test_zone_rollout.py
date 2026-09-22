"""Regression tests for the actual Production proxy transformation."""

import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
OPERATOR = runpy.run_path(str(ROOT / "deploy/zone-access/rollout.py"))
RELEASE = runpy.run_path(str(ROOT / "deploy/release/release.py"))


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_zone_proxy_retains_device_guard_and_direct_receiver(environment):
    _, _, _, old_api, direct, api, front = OPERATOR["ENVIRONMENTS"][environment]
    name = "green-mind.ch.conf" if environment == "production" else "test.green-mind.ch.conf"
    source = (ROOT / "nginx" / name).read_text()
    if "    # Independent Direct receiver;" not in source:
        source = source.replace(
            "    # BEGIN GREENMIND",
            f"    # Independent Direct receiver;\n    location ^~ /api/v1/direct-ingest/ {{\n        proxy_pass http://127.0.0.1:{direct};\n    }}\n\n    # BEGIN GREENMIND",
            1,
        )
    source, _ = RELEASE["proposal"](
        source, environment, old_api + 6, 3006 if environment == "production" else 3009
    )
    candidate = OPERATOR["render"](source, environment)
    assert OPERATOR["GUARD"]["block"](environment).replace(f":{old_api}", f":{api}") in candidate
    assert f"proxy_pass http://127.0.0.1:{direct};" in candidate
    assert f"proxy_pass http://127.0.0.1:{api + 2};" in candidate
    assert f"proxy_pass http://127.0.0.1:{api + 1};" in candidate
    assert f"proxy_pass http://127.0.0.1:{front};" in candidate
    assert "error_page 410 = @gateway_continuity_unavailable;" in candidate
    assert "return 503" in candidate
    assert candidate.count("location = /api/v1/direct-ingest/devices") == 1


def test_zone_proxy_refuses_missing_gateway_protection():
    with pytest.raises(RuntimeError):
        OPERATOR["render"]("server {}", "production")
