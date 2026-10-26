"""Prepare a separate private SSH socket relay without restarting existing services."""

import argparse
import grp
import json
import os
import pwd
import stat
import subprocess
from pathlib import Path

SOURCE = "Biolingo"
ARCHIVE = "ubuntu-4gb-fsn1-1"
DIRECTORY = Path("/run/greenmind-direct-archive-relay-20261012")
UNIT = Path("/etc/systemd/system/greenmind-direct-archive-relay-20261012.service")
TMPFILES = Path("/etc/tmpfiles.d/greenmind-direct-archive-relay-20261012.conf")
GROUP = "greenmind-direct-archive-read"
PERMISSIONS = Path(
    "/etc/systemd/system/greenmind-direct-relay-permissions-20261012.service"
)
HELPER = Path("/opt/greenmind/direct-relay-20261012/direct-relay-permissions.py")
PERMISSIONS_CONTENT = """[Unit]
Description=GreenMind private Direct relay socket permissions
After=systemd-tmpfiles-setup.service
[Service]
Type=simple
User=root
SupplementaryGroups=greenmind-direct-archive-read
ExecStart=/usr/bin/python3 /opt/greenmind/direct-relay-20261012/direct-relay-permissions.py
MemoryMax=16M
MemorySwapMax=0
CPUQuota=1%
TasksMax=2
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/run/greenmind-direct-archive-relay-20261012
RestrictAddressFamilies=AF_UNIX
CapabilityBoundingSet=CAP_FOWNER
Restart=no
"""
CONTENT = """[Unit]
Description=GreenMind private verified Direct archive read transport
After=network-online.target greenmind-archive-reader-broker-20261010.service
Wants=network-online.target
[Service]
Type=simple
User=greenmind-archive-read
Group=greenmind-archive-read
Slice=greenmind-archive.slice
LoadCredential=link-key:/etc/greenmind/source-link/key
LoadCredential=known-hosts:/etc/greenmind/source-link/known_hosts
ExecStart=/usr/bin/ssh -F /dev/null -N -T -i %d/link-key -o UserKnownHostsFile=%d/known-hosts -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ConnectTimeout=10 -o StreamLocalBindMask=0177 -o StreamLocalBindUnlink=yes -R /run/greenmind-direct-archive-relay-20261012/broker.sock:/run/greenmind-archive-reader-20261010/broker.sock greenmind-archive-link@188.245.247.156
Restart=on-failure
RestartSec=15
MemoryMax=24M
MemorySwapMax=0
CPUQuota=5%
TasksMax=8
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=
"""


def install_new_or_identical(path, content):
    encoded = content.encode()
    if path.exists():
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o022
            or info.st_size != len(encoded)
            or path.read_bytes() != encoded
        ):
            raise RuntimeError("Refusing to replace an existing operator file")
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as body:
        body.write(encoded)
        body.flush()
        os.fsync(body.fileno())


def prepare_source():
    if os.uname().nodename != SOURCE:
        raise RuntimeError("Source host required")
    account = pwd.getpwnam("greenmind-archive-link")
    if account.pw_shell != "/usr/sbin/nologin":
        raise RuntimeError("Existing forwarding-only identity required")
    try:
        group = grp.getgrnam(GROUP)
    except KeyError:
        subprocess.run(["groupadd", "--system", GROUP], check=True, timeout=10)
        group = grp.getgrnam(GROUP)
    if group.gr_mem or any(user.pw_gid == group.gr_gid for user in pwd.getpwall()):
        raise RuntimeError("Dedicated empty container read group required")
    if DIRECTORY.exists():
        info = DIRECTORY.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != account.pw_uid
            or info.st_gid != group.gr_gid
            or stat.S_IMODE(info.st_mode) != 0o2710
        ):
            raise RuntimeError("Unexpected private transport directory")
    # Apply only this new directory; do not invoke all system tmpfile policies.
    install_new_or_identical(
        TMPFILES,
        f"d {DIRECTORY} 2710 greenmind-archive-link {GROUP} -\n",
    )
    subprocess.run(
        ["systemd-tmpfiles", "--create", str(TMPFILES)], check=True, timeout=10
    )
    HELPER.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if HELPER.parent.lstat().st_uid != 0 or HELPER.parent.is_symlink():
        raise RuntimeError("Protected new helper directory required")
    install_new_or_identical(HELPER, Path(__file__).with_name(HELPER.name).read_text())
    install_new_or_identical(PERMISSIONS, PERMISSIONS_CONTENT)
    subprocess.run(
        ["systemd-analyze", "verify", str(PERMISSIONS)],
        check=True,
        capture_output=True,
        timeout=10,
    )
    subprocess.run(
        ["systemctl", "daemon-reload"], check=True, capture_output=True, timeout=10
    )
    print(
        json.dumps(
            {
                "prepared": "source_private_directory",
                "permissions_service_started": False,
                "worker_supplementary_gid": group.gr_gid,
                "receiver_changed": False,
            }
        )
    )


def prepare_archive():
    if os.uname().nodename != ARCHIVE:
        raise RuntimeError("Archive host required")
    account = pwd.getpwnam("greenmind-archive-read")
    broker = Path("/run/greenmind-archive-reader-20261010/broker.sock")
    if not broker.is_socket() or broker.lstat().st_uid != account.pw_uid:
        raise RuntimeError("Existing private verified broker required")
    for path in (
        Path("/etc/greenmind/source-link/key"),
        Path("/etc/greenmind/source-link/known_hosts"),
    ):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_size > 65536:
            raise RuntimeError("Existing protected SSH transport credentials required")
        if path.name == "key" and info.st_mode & 0o077:
            raise RuntimeError("Private SSH key permissions required")
    install_new_or_identical(UNIT, CONTENT)
    subprocess.run(
        ["systemd-analyze", "verify", str(UNIT)],
        check=True,
        capture_output=True,
        timeout=10,
    )
    subprocess.run(
        ["systemctl", "daemon-reload"], check=True, capture_output=True, timeout=10
    )
    print(
        json.dumps(
            {"prepared": "archive_relay_inactive", "started": False, "deleted_files": 0}
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", choices=("source", "archive"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Operator root authentication required")
    (prepare_source if args.host == "source" else prepare_archive)()


if __name__ == "__main__":
    main()
