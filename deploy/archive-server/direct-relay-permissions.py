"""Set only the new SSH relay socket's group access; no data or network operations."""

import grp
import os
import pwd
import stat
import time
from pathlib import Path

DIRECTORY = Path("/run/greenmind-direct-archive-relay-20261012")
SOCKET = DIRECTORY / "broker.sock"


def normalize(path, uid, gid):
    info = path.lstat()
    if (
        not stat.S_ISSOCK(info.st_mode)
        or info.st_uid != uid
        or info.st_gid != gid
        or stat.S_IMODE(info.st_mode) not in {0o600, 0o660}
    ):
        raise RuntimeError("Unexpected private relay socket")
    if stat.S_IMODE(info.st_mode) == 0o600:
        os.chmod(path, 0o660, follow_symlinks=False)
        return True
    return False


def main():
    if os.geteuid() != 0 or os.uname().nodename != "Biolingo":
        raise RuntimeError("Source operator service required")
    account = pwd.getpwnam("greenmind-archive-link")
    group = grp.getgrnam("greenmind-direct-archive-read")
    directory = DIRECTORY.lstat()
    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != account.pw_uid
        or directory.st_gid != group.gr_gid
        or stat.S_IMODE(directory.st_mode) != 0o2710
    ):
        raise RuntimeError("Expected private transport directory required")
    while True:
        try:
            normalize(SOCKET, account.pw_uid, group.gr_gid)
        except FileNotFoundError:
            pass
        time.sleep(1)


if __name__ == "__main__":
    main()
