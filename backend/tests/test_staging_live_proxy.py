import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "deploy/release/staging_live_proxy.py"
SPEC = importlib.util.spec_from_file_location("staging_live_proxy", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONFIG = """server {
    server_name test.green-mind.ch;
    location = /api/v1/direct-ingest/devices {
        proxy_pass http://127.0.0.1:14001;
    }
    location ^~ /api/v1/direct-ingest/ {
        client_max_body_size 64k;
        proxy_pass http://127.0.0.1:14002;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        add_header Cache-Control "private, no-store" always;
    }
    location /api/ { proxy_pass http://127.0.0.1:14000; }
}
"""


def test_device_prefix_isolated_without_changing_raw_or_legacy_contract():
    result = MODULE.proposal(CONFIG)
    insertion = result[result.index(MODULE.BEGIN) : result.index(MODULE.END)]
    assert "location ^~ /api/v1/direct-ingest/devices/" in insertion
    assert "proxy_pass http://127.0.0.1:14001;" in insertion
    assert "X-Forwarded-Proto $scheme" in insertion
    assert "X-Forwarded-For $remote_addr" in insertion
    assert 'Cache-Control "private, no-store"' in insertion
    without = result.replace(insertion + MODULE.END + "\n\n", "")
    assert without == CONFIG
    assert MODULE.proposal(result) == result


@pytest.mark.parametrize(
    "changed",
    [
        CONFIG.replace("test.green-mind.ch", "green-mind.ch"),
        CONFIG.replace("test.green-mind.ch;", "test.green-mind.ch green-mind.ch;"),
        CONFIG.replace(":14001", ":14002"),
        CONFIG.replace(":14001", ":14001/path"),
        CONFIG.replace("location =", "location ^~"),
        CONFIG + "\n" + CONFIG,
        CONFIG + "\n    location ^~ /api/v1/direct-ingest/devices/ { return 503; }",
    ],
)
def test_unsupported_or_unsafe_configuration_is_rejected(changed):
    with pytest.raises(ValueError):
        MODULE.proposal(changed)


def test_different_existing_isolation_is_not_silently_replaced():
    result = MODULE.proposal(CONFIG).replace(
        "# BEGIN GREENMIND STAGING LIVE ISOLATION\n",
        "# BEGIN GREENMIND STAGING LIVE ISOLATION\n# concurrent operator edit\n",
    )
    with pytest.raises(ValueError):
        MODULE.proposal(result)
