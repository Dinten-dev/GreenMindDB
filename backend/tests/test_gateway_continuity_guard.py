"""The nginx shield blocks cloud actions independently of the deployed backend."""

import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/release/gateway_guard.py"
pytestmark = pytest.mark.skipif(not SCRIPT.is_file(), reason="requires repository operator package")
GUARD = runpy.run_path(str(SCRIPT)) if SCRIPT.is_file() else {}


@pytest.mark.parametrize(
    "environment,name", [("production", "green-mind.ch"), ("staging", "test.green-mind.ch")]
)
def test_guard_is_present_and_survives_repeated_render(environment, name):
    installed = (ROOT / f"nginx/{name}.conf").read_text()
    GUARD["require_guard"](installed, environment)
    assert GUARD["render"](installed, environment) == installed
    unguarded = installed.replace(GUARD["block"](environment), "")
    with pytest.raises(RuntimeError, match="before any server release"):
        GUARD["require_guard"](unguarded, environment)
    assert GUARD["render"](unguarded, environment) == installed
    # These upload/login/WebSocket routes stay on their old receiver unchanged.
    tail = installed.split("    # API → Backend directly", 1)[1]
    assert GUARD["render"](unguarded, environment).endswith(tail)


def test_modified_guard_cannot_be_silently_accepted():
    original = "    # API → Backend directly\n"
    guarded = GUARD["render"](original, "production")
    changed = guarded.replace("return 503", "return 200", 1)
    with pytest.raises(RuntimeError):
        GUARD["render"](changed, "production")


@pytest.mark.parametrize("failure", [None, "syntax", "reload", "drift"])
def test_actual_guard_cli_preserves_site_on_failure(tmp_path, monkeypatch, failure):
    import os
    import signal
    import subprocess
    import sys

    main = GUARD["main"]
    namespace = main.__globals__
    sites = tmp_path / "sites"
    sites.mkdir()
    site = sites / "greenmind-staging"
    original = "    # API → Backend directly\n"
    site.write_text(original)
    real_path = Path

    def paths(value):
        if value == "/etc/nginx/sites-available":
            return sites
        if value == "/run/lock/greenmind-visualization-proxy.lock":
            return tmp_path / "proxy.lock"
        return real_path(value)

    monkeypatch.setitem(namespace, "Path", paths)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "umask", lambda _: 0)
    monkeypatch.setattr(signal, "signal", lambda *_: None)
    package = tmp_path / "review"
    args = ["gateway_guard.py", "prepare", "--environment", "staging", "--directory", str(package)]
    monkeypatch.setattr(sys, "argv", args)
    main()
    assert site.read_text() == original
    calls = []

    def execute(command, **kwargs):
        assert command in (["nginx", "-t"], ["systemctl", "reload", "nginx"])
        calls.append(command)
        if (failure == "syntax" and len(calls) == 1) or (failure == "reload" and len(calls) == 2):
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", execute)
    if failure == "drift":
        site.write_text("other operator change")
    args[1] = "activate"
    if failure:
        with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
            main()
        assert site.read_text() == ("other operator change" if failure == "drift" else original)
        if failure == "drift":
            assert not calls
        else:
            assert calls[-2:] == [["nginx", "-t"], ["systemctl", "reload", "nginx"]]
    else:
        main()
        GUARD["require_guard"](site.read_text(), "staging")
        assert calls == [["nginx", "-t"], ["systemctl", "reload", "nginx"]]
