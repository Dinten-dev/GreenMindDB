"""The admin archive bridge never opens a host connection on Staging."""

from unittest.mock import patch

import pytest

from app.archive_monitor import ArchiveBridgeUnavailable, archive_bridge


def test_bridge_requires_explicit_socket(monkeypatch):
    monkeypatch.delenv("ARCHIVE_MONITOR_SOCKET", raising=False)
    with patch("socket.socket") as sock, pytest.raises(ArchiveBridgeUnavailable):
        archive_bridge("GET", "/status")
    sock.assert_not_called()


def test_bridge_rejects_other_operations(monkeypatch):
    monkeypatch.setenv("ARCHIVE_MONITOR_SOCKET", "/no/socket")
    with patch("socket.socket") as sock, pytest.raises(ArchiveBridgeUnavailable):
        archive_bridge("POST", "/delete")
    sock.assert_not_called()


def test_host_bridge_refuses_enabled_deletion():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "host_archive_bridge", Path(__file__).parents[2] / "deploy/archive-monitor/bridge.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for enabled in module.FLAGS:
        values = dict.fromkeys(module.FLAGS, "false")
        values[enabled] = "true"
        with (
            patch.object(module, "config", return_value=values),
            patch.object(module, "run") as run,
        ):
            assert module.copy_safe() is False
        run.assert_not_called()


def test_quota_uses_archive_account_home_and_prefixed_configuration():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "host_archive_bridge", Path(__file__).parents[2] / "deploy/archive-monitor/bridge.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    values = {
        "RAW_ARCHIVE_SFTP_KEY": "/etc/key",
        "RAW_ARCHIVE_SFTP_KNOWN_HOSTS": "/etc/hosts",
        "RAW_ARCHIVE_SFTP_USER": "archive",
        "RAW_ARCHIVE_SFTP_HOST": "example.invalid",
    }
    with (
        patch.object(module, "config", return_value=values),
        patch.object(
            module, "run", return_value="1073741824 45771520 1027970304 1027970304 4%\n"
        ) as run,
    ):
        assert module.quota() == (1099511627776, 46870036480)
    assert run.call_args.kwargs["data"] == "df .\n"
