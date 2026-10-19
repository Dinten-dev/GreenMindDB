"""Consistent staging-only cold export with an independent 14-minute rollback."""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from urllib.request import urlopen

ROOT = Path("/home/traver/greenmind-archive-rollout-20261008/staging-preparation-v2")
PROXY = Path("/etc/nginx/sites-available/greenmind-staging")
PROXY_SHA = "6b0737287aa88ffaa15a26cf5351f78088a9df3198837fdc94c9dc2ae08e3b2c"


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def health():
    for port in (8120, 8003, 8000):
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            assert response.status == 200


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    assert os.geteuid() == 0 and os.uname().nodename == "Biolingo"
    manifest = json.loads((ROOT / "manifest.json").read_text())
    names = manifest["pause_containers"]
    assert all("staging" in name and "production" not in name for name in names)
    assert len(names) == len(set(names)) and 1 <= len(names) <= 24
    if args.rollback:
        if (ROOT / "cutover-accepted.json").exists():
            return
        if digest(PROXY) != PROXY_SHA:
            subprocess.run(
                [
                    "python3",
                    str(Path(__file__).with_name("staging-proxy.py")),
                    "--mode",
                    "rollback",
                ],
                check=True,
            )
        subprocess.run(
            ["docker", "start", *names], check=True, stdout=subprocess.DEVNULL
        )
        print(json.dumps({"staging_restarted": True, "deletion_enabled": False}))
        return
    assert digest(PROXY) == PROXY_SHA, "Staging proxy changed since inspection"
    assert (ROOT / "images-evidence.json").is_file(), (
        "Prepare complete images before downtime"
    )
    assert not (ROOT / "data.tar").exists(), "Never overwrite an earlier cold export"
    health()
    counts = {}
    for database, tables in {
        "plantdb_staging": ("zone", "sensor", "wav_file"),
        "greenmind_direct_staging": ("direct_device", "direct_revision"),
    }.items():
        counts[database] = {}
        for table in tables:
            value = subprocess.check_output(
                [
                    "docker",
                    "exec",
                    "gm-staging-postgres-1",
                    "psql",
                    "-U",
                    "plantuser",
                    "-d",
                    database,
                    "-Xt",
                    "--set",
                    "ON_ERROR_STOP=on",
                    "-c",
                    f"BEGIN READ ONLY; SET LOCAL statement_timeout=8000; SELECT count(*) FROM {table}; COMMIT;",
                ],
                text=True,
                timeout=12,
            )
            counts[database][table] = int(
                next(v for v in value.splitlines() if v.strip().isdigit())
            )
    for name in names:
        networks = json.loads(
            subprocess.check_output(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{json .NetworkSettings.Networks}}",
                    name,
                ]
            )
        )
        assert set(networks) == {"gm-staging_default"}, (
            "Unexpected shared staging dependency"
        )
    subprocess.run(
        [
            "systemd-run",
            "--unit=greenmind-staging-move-watchdog-20261008",
            "--on-active=840",
            "/usr/bin/python3",
            str(Path(__file__).resolve()),
            "--rollback",
        ],
        check=True,
    )
    core = ["gm-staging-postgres-1", "gm-staging-minio-1"]
    assert set(core) <= set(names)
    try:
        subprocess.run(
            ["docker", "stop", "--time", "30", *[n for n in names if n not in core]],
            check=True,
            timeout=65,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            ["docker", "stop", "--time", "30", *core],
            check=True,
            timeout=65,
            stdout=subprocess.DEVNULL,
        )
        health()
        files = [m["source"] for m in manifest["mounts"]]
        assert all(
            v.startswith(
                (
                    "/home/traver/greenmind-",
                    "/mnt/HC_Volume_106755700/docker/volumes/gm-staging_",
                )
            )
            for v in files
        )
        target = ROOT / "data.tar"
        with target.open("xb") as output:
            os.fchmod(output.fileno(), 0o600)
            subprocess.run(
                [
                    "nice",
                    "-n",
                    "19",
                    "ionice",
                    "-c",
                    "3",
                    "tar",
                    "-C",
                    "/",
                    "-cpf",
                    "-",
                    *[v.lstrip("/") for v in files],
                ],
                stdout=output,
                check=True,
                timeout=120,
            )
            output.flush()
            os.fsync(output.fileno())
        assert target.stat().st_size < 4 * 1024**3, "Unexpected staging dataset size"
        health()
        evidence = {
            "sha256": digest(target),
            "bytes": target.stat().st_size,
            "source_containers_stopped": names,
            "consistent_cold_copy": True,
            "deletion_enabled": False,
            "watchdog_seconds": 840,
            "table_counts": counts,
        }
        with (ROOT / "data-evidence.json").open("x") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(evidence, output, indent=2)
        print(json.dumps(evidence))
    except BaseException:
        subprocess.run(
            ["docker", "start", *names], check=True, stdout=subprocess.DEVNULL
        )
        raise


if __name__ == "__main__":
    main()
