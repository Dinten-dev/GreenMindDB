"""A healthy old receiver cannot mask a failed new production receiver."""

import json
from types import SimpleNamespace

import pytest

from app.raw_archive import source_probe


class Config:
    parents = ()

    def __init__(self, body, *, owner=0, mode=0o644, symlink=False):
        self.body, self.owner, self.mode, self.symlink = body, owner, mode, symlink

    def lstat(self):
        return SimpleNamespace(st_uid=self.owner, st_mode=self.mode, st_size=len(self.body))

    def is_symlink(self):
        return self.symlink

    def is_file(self):
        return True

    def read_text(self):
        return self.body


@pytest.mark.parametrize("ports", [[8120], [8120, 8152]])
def test_only_reviewed_receivers_allowed(ports):
    assert source_probe.gateway_ports(Config(json.dumps({"schema": 1, "ports": ports}))) == tuple(
        ports
    )


@pytest.mark.parametrize(
    "config",
    [
        Config('{"schema":1,"ports":[8152]}'),
        Config('{"schema":1,"ports":[8120,9000]}'),
        Config('{"schema":1,"ports":[8120]}', owner=1000),
        Config('{"schema":1,"ports":[8120]}', mode=0o666),
        Config('{"schema":1,"ports":[8120]}', symlink=True),
        Config(" " * 1025),
    ],
)
def test_untrusted_or_broader_configuration_rejected(config):
    with pytest.raises(ValueError):
        source_probe.gateway_ports(config)


def test_missing_config_retains_original(tmp_path):
    assert source_probe.gateway_ports(tmp_path / "absent") == (8120,)


def test_new_receiver_failure_is_exposed(monkeypatch, tmp_path):
    memory = tmp_path / "meminfo"
    memory.write_text("MemAvailable: 900000 kB\n")
    monkeypatch.setattr(source_probe, "Path", lambda _: memory)
    monkeypatch.setattr(source_probe, "gateway_ports", lambda: (8120, 8152))
    monkeypatch.setenv("GREENMIND_SOURCE_ID", "production-main")
    calls = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    def get(url, timeout):
        calls.append(url)
        if ":8152/" in url:
            raise OSError("failed")
        return Response()

    monkeypatch.setattr(source_probe, "urlopen", get)
    result = source_probe.snapshot([tmp_path])
    assert result["routes"] == {"8120": 0, "8003": 200, "8000": 200}
    assert result["gateway_receivers"] == {"8120": 200, "8152": 0}
    assert len(calls) == 4
