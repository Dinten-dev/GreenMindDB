"""Provider subaccount rooted at greenmind-raw; receipts retain primary identity."""

import os
import re
from pathlib import Path

from .policy import ArchiveBlocked
from .storage import StorageBox

PREFIX = "RAW_ARCHIVE_READ_ONLY_SFTP_"
FIELDS = ("USER", "HOST", "KEY", "KNOWN_HOSTS")


class ReadOnlyBox:
    """Only verified-key GETs; no publish, delete, shell or generic batch method."""

    def __init__(self, *, user, host, key, known_hosts):
        match = re.fullmatch(r"(u[0-9]+)-sub[0-9]+", user)
        if match is None or host != user + ".your-storagebox.de":
            raise ArchiveBlocked("Dedicated matching Storage Box subaccount required")
        primary = match.group(1)
        self.identity = f"sftp://{primary}@{primary}.your-storagebox.de:23/greenmind-raw"
        self.__transport = StorageBox(
            host=host, user=user, key=key, known_hosts=known_hosts, kilobits=2048
        )

    def download(self, key, path, max_bytes):
        if path.exists() or path.is_symlink() or not 0 < max_bytes <= 64 * 1024**2:
            raise ArchiveBlocked("Download needs a new bounded private path")
        # The provider roots this subaccount at greenmind-raw. Keep logical
        # receipt identity; never add a second greenmind-raw prefix remotely.
        remote = self.__transport.path(key).removeprefix("greenmind-raw/")
        self.__transport.run(
            f"get {remote} {self.__transport.quote(path)}\n",
            output=path,
            max_bytes=max_bytes,
            missing_path=remote,
        )
        if path.stat().st_size > max_bytes:
            raise ArchiveBlocked("Remote object exceeds allowed size")

    def close(self):
        self.__transport.close()


def configured_read_only_box():
    """Partial configuration blocks instead of falling back to upload credentials."""
    if not any(name.startswith(PREFIX) for name in os.environ):
        return None
    values = {name: os.environ.get(PREFIX + name, "") for name in FIELDS}
    if any(not value or value.startswith("FILL_") for value in values.values()):
        raise ArchiveBlocked("Complete separate read-only Storage Box configuration required")
    key = Path(values["KEY"])
    known_hosts = Path(values["KNOWN_HOSTS"])
    if not key.is_absolute() or not known_hosts.is_absolute():
        raise ArchiveBlocked("Explicit absolute read-only credential paths required")
    upload_key = os.environ.get("RAW_ARCHIVE_SFTP_KEY")
    if upload_key and Path(upload_key).is_file() and key.is_file() and key.samefile(upload_key):
        raise ArchiveBlocked("Read-only access must not reuse the upload key")
    return ReadOnlyBox(user=values["USER"], host=values["HOST"], key=key, known_hosts=known_hosts)
