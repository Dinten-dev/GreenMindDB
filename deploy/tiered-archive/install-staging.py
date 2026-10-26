"""Install a hash-verified, isolated staging worker; never activates deletion."""

import argparse
import hashlib
import json
import os
import pwd
import shutil
import subprocess
from pathlib import Path


def parse_env(path):
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            if key in values:
                raise ValueError("Duplicate profile key")
            values[key] = value
    return values


def validate(profile, schedule):
    from urllib.parse import urlparse

    if (
        profile.get("RAW_ARCHIVE_ENVIRONMENT") != "staging"
        or profile.get("TIERED_ARCHIVE_ENVIRONMENT") != "staging"
    ):
        raise ValueError("Only explicit staging profiles may be installed")
    forbidden = (
        "RETENTION_ENABLED",
        "DIRECT_RETENTION_ENABLED",
        "VISUAL_PRUNE_ENABLED",
        "RAW_ARCHIVE_DELETE_ENABLED",
        "TIERED_ARCHIVE_DELETE_ENABLED",
    )
    if any(profile.get(key, "false").lower() != "false" for key in forbidden):
        raise ValueError("Deletion/pruning must be disabled")
    if schedule.get("environment") != "staging" or schedule.get("schema") != 1:
        raise ValueError("Staging schedule required")
    for source in ("GATEWAY", "DIRECT"):
        value = profile.get("RAW_ARCHIVE_" + source + "_DATABASE_URL", "")
        parsed = urlparse(value)
        if (
            not parsed.scheme.startswith("postgresql")
            or "staging" not in parsed.path.lower()
        ):
            raise ValueError("Source database is not an explicit staging copy")
    if not profile.get("RAW_ARCHIVE_DIRECT_S3_BUCKET", "").startswith(
        "greenmind-direct-staging"
    ):
        raise ValueError("Staging Direct bucket required")
    for key in profile:
        if "WRITER_" in key:
            raise ValueError("No writer credentials in a copy-only staging worker")
    state = Path(profile.get("TIERED_ARCHIVE_STATE_DIR", ""))
    if state != Path("/var/lib/greenmind/tiered-archive/staging"):
        raise ValueError("Use the isolated staging state path")
    if Path(profile.get("RAW_ARCHIVE_STATE_DIR", "")) != state / "raw":
        raise ValueError("Raw WAV receipts must use isolated staging state")


def verify(release, expected_sha256):
    if (
        hashlib.sha256((release / "release.json").read_bytes()).hexdigest()
        != expected_sha256
    ):
        raise ValueError("Release manifest differs from reviewed fingerprint")
    manifest = json.loads((release / "release.json").read_text())
    members = list(release.rglob("*"))
    if any(path.is_symlink() for path in members):
        raise ValueError("Release contains symbolic links")
    if {
        path.relative_to(release).as_posix() for path in members if path.is_file()
    } != set(manifest["files"]) | {"release.json"}:
        raise ValueError("Unexpected or missing release content")
    for name, sha in manifest["files"].items():
        path = release / name
        if (
            path.is_symlink()
            or not path.is_relative_to(release)
            or ".." in Path(name).parts
        ):
            raise ValueError("Unsafe release member")
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError("Release content changed")
    return manifest


def validate_reader(profile, writer):
    if (
        profile.get("TIERED_ARCHIVE_ENVIRONMENT") != "staging"
        or profile.get("RAW_ARCHIVE_ENVIRONMENT") != "staging"
    ):
        raise ValueError("Only staging historical reader profiles may be installed")
    if profile.get("TIERED_ARCHIVE_READS_ENABLED") != "true":
        raise ValueError("Explicit historical reader profile required")
    for name in (
        "TIERED_ARCHIVE_DELETE_ENABLED",
        "RAW_ARCHIVE_DELETE_ENABLED",
        "RETENTION_ENABLED",
        "DIRECT_RETENTION_ENABLED",
        "VISUAL_PRUNE_ENABLED",
    ):
        if profile.get(name, "false").lower() != "false":
            raise ValueError("Historical read service cannot delete")
    if any(
        name.startswith("RAW_ARCHIVE_SFTP_") or "DATABASE_URL" in name
        for name in profile
    ):
        raise ValueError(
            "No upload or database credentials in the historical read service"
        )
    if profile.get("TIERED_ARCHIVE_STATE_DIR") != writer["TIERED_ARCHIVE_STATE_DIR"]:
        raise ValueError("Historical read catalogue must match isolated staging writer")
    if (
        profile.get("TIERED_ARCHIVE_READ_BROKER_SOCKET")
        != "/run/greenmind-tiered-archive-staging/history.sock"
    ):
        raise ValueError("Isolated staging reader socket required")
    for suffix in ("USER", "HOST", "KEY", "KNOWN_HOSTS"):
        if not profile.get("RAW_ARCHIVE_READ_ONLY_SFTP_" + suffix):
            raise ValueError("Complete dedicated read-only Storage Box access required")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--read-profile", type=Path)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    release = args.release.resolve()
    manifest = verify(release, args.manifest_sha256)
    profile = parse_env(args.profile)
    schedule = json.loads(args.schedule.read_text())
    validate(profile, schedule)
    read_profile = parse_env(args.read_profile) if args.read_profile else None
    if read_profile is not None:
        validate_reader(read_profile, profile)
    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "REVIEW_ONLY",
                    "runtime_sha256": manifest["runtime_sha256"],
                    "deletion": False,
                    "historical_reader": read_profile is not None,
                }
            )
        )
        return
    if os.geteuid() != 0:
        raise PermissionError("Administrator authentication required")
    account = pwd.getpwnam("greenmind-archive")
    reader = (
        pwd.getpwnam("greenmind-archive-read") if read_profile is not None else None
    )
    destination = Path("/opt/greenmind/tiered-archive") / manifest["runtime_sha256"]
    if destination.exists():
        raise FileExistsError("Immutable release already exists; inspect before retry")
    config = Path("/etc/greenmind/tiered-archive")
    if (config / "staging.env").exists():
        raise FileExistsError("Existing staging profile retained")
    state = Path(profile["TIERED_ARCHIVE_STATE_DIR"])
    if reader and state.parent.exists():
        parent = state.parent.stat()
        if not (
            parent.st_mode & 0o001
            or parent.st_gid == reader.pw_gid
            and parent.st_mode & 0o010
        ):
            raise PermissionError(
                "Reader needs directory traversal on the staging parent"
            )
    for path in (
        state / "cache",
        config / "staging-read.env",
        Path("/etc/systemd/system/greenmind-tiered-archive-staging.service"),
        Path("/etc/systemd/system/greenmind-tiered-read-staging.service"),
    ):
        if path.exists():
            raise FileExistsError("Existing staging resource retained: " + str(path))
    shutil.copytree(release, destination)
    verify(destination, args.manifest_sha256)
    subprocess.run(
        [str(args.python), "-m", "venv", str(destination / "python")], check=True
    )
    python = destination / "python/bin/python"
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--require-hashes",
            "-r",
            str(destination / "backend/requirements.lock"),
        ],
        check=True,
    )
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--require-hashes",
            "-r",
            str(destination / "backend/requirements-archive.lock"),
        ],
        check=True,
    )
    config.mkdir(mode=0o750, parents=True, exist_ok=True)
    profile.pop("PYTHONHOME", None)
    profile.update(
        TIERED_ARCHIVE_ENABLED="true",
        TIERED_ARCHIVE_DELETE_ENABLED="false",
        PYTHONPATH=str(destination / "backend"),
    )
    if reader:
        profile["TIERED_ARCHIVE_READ_GID"] = str(reader.pw_gid)
    for name, body in [
        (
            "staging.env",
            "".join(key + "=" + value + "\n" for key, value in sorted(profile.items())),
        ),
        ("staging-schedule.json", json.dumps(schedule, sort_keys=True)),
    ]:
        path = config / name
        with path.open("x") as out:
            out.write(body)
        path.chmod(0o600)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(state, account.pw_uid, account.pw_gid)
    raw_state = Path(profile["RAW_ARCHIVE_STATE_DIR"])
    raw_state.mkdir(mode=0o700, exist_ok=True)
    os.chown(raw_state, account.pw_uid, account.pw_gid)
    if reader:
        os.chown(state, account.pw_uid, reader.pw_gid)
        state.chmod(0o750)
        cache = state / "cache"
        cache.mkdir(mode=0o700)
        os.chown(cache, reader.pw_uid, reader.pw_gid)
        read_profile.pop("PYTHONHOME", None)
        read_profile["PYTHONPATH"] = str(destination / "backend")
        read_path = config / "staging-read.env"
        with read_path.open("x") as out:
            out.write(
                "".join(
                    key + "=" + value + "\n"
                    for key, value in sorted(read_profile.items())
                )
            )
        read_path.chmod(0o600)
    # systemd reads EnvironmentFile as root, before dropping to the service user.
    service = (
        destination / "deploy/tiered-archive/greenmind-tiered-archive.service"
    ).read_text()
    service = service.replace("/opt/greenmind/tiered-archive/current", str(destination))
    service = service.replace(
        "/etc/greenmind/tiered-archive.env", str(config / "staging.env")
    )
    if reader:
        service = service.replace(
            "Group=greenmind-archive\n",
            "Group=greenmind-archive\nSupplementaryGroups=greenmind-archive-read\n",
        )
    service = service.replace(
        "/etc/greenmind/tiered-archive-schedule.json",
        str(config / "staging-schedule.json"),
    )
    service = service.replace(
        "ReadWritePaths=/var/lib/greenmind/tiered-archive /var/lib/greenmind/archive-copy-20261010 /run/greenmind-archive",
        "ReadWritePaths=" + str(state) + " " + profile["RAW_ARCHIVE_STATE_DIR"],
    )
    unit = Path("/etc/systemd/system/greenmind-tiered-archive-staging.service")
    with unit.open("x") as out:
        out.write(service)
    if reader:
        read_unit = (
            destination / "deploy/tiered-archive/greenmind-tiered-read.service"
        ).read_text()
        read_unit = read_unit.replace(
            "/opt/greenmind/tiered-archive/current", str(destination)
        )
        read_unit = read_unit.replace(
            "/etc/greenmind/tiered-archive-read.env", str(config / "staging-read.env")
        )
        read_unit = read_unit.replace(
            "greenmind-tiered-archive", "greenmind-tiered-archive-staging"
        )
        read_unit = read_unit.replace(
            "/var/lib/greenmind/tiered-archive/cache", str(state / "cache")
        )
        with Path("/etc/systemd/system/greenmind-tiered-read-staging.service").open(
            "x"
        ) as out:
            out.write(read_unit)
    subprocess.run(["/usr/bin/systemctl", "daemon-reload"], check=True)
    print(
        json.dumps(
            {
                "status": "STAGING_INSTALLED_NOT_STARTED",
                "runtime_sha256": manifest["runtime_sha256"],
                "unit": unit.name,
                "deletion": False,
            }
        )
    )


if __name__ == "__main__":
    main()
