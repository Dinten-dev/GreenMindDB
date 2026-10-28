"""Standalone private read service; no source writer or upload credential fallback."""

import os
import socket
import stat
import threading
from contextlib import closing
from pathlib import Path

from app.raw_archive.health import SafetyPause
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.read_broker import Handler, Server
from app.raw_archive.read_only_box import configured_read_only_box
from app.raw_archive.worker import configuration

from .latency import health_probe
from .resources import monitored, pause


class ReadHandler(Handler):
    def authorized_peer(self, uid):
        return super().authorized_peer(uid) or uid in getattr(self.server, "read_peer_uids", ())

    def do_POST(self):
        state = Path(os.environ["TIERED_ARCHIVE_STATE_DIR"])
        if (state / "PAUSE").exists() or (state / "control/PAUSE").exists():
            self.send_error(503, "Staging archive paused")
            return
        if self.path not in {"/tiered-series", "/tiered-features", "/tiered-revision"}:
            self.send_error(404)
            return
        super().do_POST()


def peer_uids():
    """Explicit API process identities; legacy brokers keep their own-UID rule."""
    value = os.environ.get("TIERED_ARCHIVE_READ_PEER_UIDS", "")
    if not value:
        return frozenset()
    values = value.split(",")
    if len(values) > 8 or any(
        not 1 <= len(part) <= 10
        or not part.isascii()
        or not part.isdecimal()
        or not 1 <= int(part) <= 2**32 - 2
        for part in values
    ):
        raise ArchiveBlocked("Explicit non-root historical API peer UIDs required")
    return frozenset(map(int, values))


def socket_permissions(path, peers):
    if peers:
        parent = path.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or parent.st_gid != os.getgid()
            or stat.S_IMODE(parent.st_mode) != 0o750
        ):
            raise ArchiveBlocked("Historical API socket needs its private owner/group directory")
    path.chmod(0o660 if peers else 0o600)


def main():
    if os.environ.get("TIERED_ARCHIVE_READS_ENABLED") != "true":
        raise ArchiveBlocked("Historical reads disabled")
    if os.environ.get("TIERED_ARCHIVE_DELETE_ENABLED", "false") != "false":
        raise ArchiveBlocked("Read service cannot delete")
    if any(name.startswith("RAW_ARCHIVE_SFTP_") for name in os.environ):
        raise ArchiveBlocked("No upload credentials in historical read service")
    peers = peer_uids()
    if peers and not hasattr(socket, "SO_PEERCRED"):
        raise ArchiveBlocked("Historical API peers require Linux process credentials")
    destination = configured_read_only_box()
    if destination is None:
        raise ArchiveBlocked("Dedicated read-only Storage Box subaccount required")
    config = configuration()
    if config.delete_enabled:
        raise ArchiveBlocked("Raw deletion disabled in read service")
    path = Path(os.environ["TIERED_ARCHIVE_READ_BROKER_SOCKET"])
    if not path.is_absolute() or not path.parent.is_dir():
        raise ArchiveBlocked("Private runtime socket directory required")
    if path.exists():
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ArchiveBlocked("Unexpected runtime socket")
        path.unlink()
    os.umask(0o077)
    try:
        probe = health_probe("reader")
        health_lock = threading.Lock()
        control = Path(os.environ["TIERED_ARCHIVE_STATE_DIR"]) / "control"

        def checked_health():
            with health_lock:
                if (control / "PAUSE").exists():
                    raise SafetyPause("manual_pause")
                try:
                    if not probe():
                        raise SafetyPause(probe.last.get("reason", "receiver_headroom"))
                except Exception as error:
                    reason = error.code if isinstance(error, SafetyPause) else "health_probe_error"
                    pause(control, "reader", [reason])
                    raise SafetyPause(reason) from None

        with (
            closing(probe),
            monitored(
                Path(os.environ["TIERED_ARCHIVE_STATE_DIR"]) / "cache",
                kind="reader",
                pause_dir=Path(os.environ["TIERED_ARCHIVE_STATE_DIR"]) / "control",
            ),
            Server(
                str(path),
                config,
                destination,
                Path(os.environ["TIERED_ARCHIVE_STATE_DIR"]) / "cache",
                health_check=checked_health,
            ) as server,
        ):
            checked_health()
            socket_permissions(path, peers)
            server.read_peer_uids = peers
            server.RequestHandlerClass = ReadHandler
            # BaseServer runs this on every loop, even with idle or busy clients.
            server.service_actions = checked_health
            server.serve_forever(poll_interval=1)
    finally:
        destination.close()


if __name__ == "__main__":
    main()
