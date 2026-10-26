"""Standalone private read service; no source writer or upload credential fallback."""

import os
import stat
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.read_broker import Handler, Server
from app.raw_archive.read_only_box import configured_read_only_box
from app.raw_archive.worker import configuration


class ReadHandler(Handler):
    def do_POST(self):
        state = Path(os.environ["TIERED_ARCHIVE_STATE_DIR"])
        if (state / "PAUSE").exists() or (state / "control/PAUSE").exists():
            self.send_error(503, "Staging archive paused")
            return
        if self.path not in {"/tiered-series", "/tiered-features", "/tiered-revision"}:
            self.send_error(404)
            return
        super().do_POST()


def main():
    if os.environ.get("TIERED_ARCHIVE_READS_ENABLED") != "true":
        raise ArchiveBlocked("Historical reads disabled")
    if os.environ.get("TIERED_ARCHIVE_DELETE_ENABLED", "false") != "false":
        raise ArchiveBlocked("Read service cannot delete")
    if any(name.startswith("RAW_ARCHIVE_SFTP_") for name in os.environ):
        raise ArchiveBlocked("No upload credentials in historical read service")
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
        with Server(
            str(path), config, destination, Path(os.environ["TIERED_ARCHIVE_STATE_DIR"]) / "cache"
        ) as server:
            path.chmod(0o600)
            server.RequestHandlerClass = ReadHandler
            server.serve_forever(poll_interval=1)
    finally:
        destination.close()


if __name__ == "__main__":
    main()
