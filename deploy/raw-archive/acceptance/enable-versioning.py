"""Consume only the human-configured operator alias; never inspect container secrets."""

import argparse
import hashlib
import json
import os
import stat
import subprocess
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from app.raw_archive.minio_versioning import BUCKETS, enable_reviewed, inspected

ROOT = Path("/home/traver/greenmind-archive-acceptance-20261007")
ALIAS = Path(
    "/home/traver/.config/greenmind/minio-operator-a3b88514f95551f1/config.json"
)
IMAGE = "sha256:69b2ec208575b69597784255eec6fa6a2985ee9e1a47f4411a51f7f5fdd193a9"


def guard():
    available = (
        next(
            int(line.split()[1])
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")
        )
        / 1024
    )
    if available < 512 or os.getloadavg()[0] > 2.4:
        raise RuntimeError("Receiver resource guard unavailable")
    for port in (8120, 8003, 8000):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=2
        ) as response:
            assert response.status == 200


def protected():
    names = (
        "gm-zones-production-2e687bec010a-application-1",
        "gm-direct-production-direct-api-1",
        "greenminddb-backend-1",
        "greenminddb-minio-1",
    )
    return subprocess.check_output(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Name}} {{.State.StartedAt}} {{.RestartCount}}",
            *names,
        ],
        text=True,
        timeout=5,
    )


def head_identity(head):
    # Enabling versioning exposes existing unversioned objects as version "null".
    # This read-only comparison does not permit deletion of those objects.
    version = head.get("VersionId")
    return {
        "VersionId": None if version in (None, "null") else str(version),
        **{
            key: str(head.get(key)) for key in ("ETag", "ContentLength", "LastModified")
        },
    }


def main():
    import boto3
    from botocore.config import Config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable", action="store_true")
    args = parser.parse_args()
    guard()
    assert os.getuid() == ALIAS.stat().st_uid and not ALIAS.is_symlink()
    assert (
        stat.S_ISREG(ALIAS.stat().st_mode)
        and stat.S_IMODE(ALIAS.stat().st_mode) == 0o600
    )
    source = Path(__file__).resolve().parents[3]
    # Contract and exact image match the successful 03.10.2026 isolated tests.
    # This is reuse of that real proof, not a claim of fresh fixture execution.
    expected = {
        "backend/app/raw_archive/storage.py": "b87ddc58580b1f33cc772ac9650129ba53bbb6e6ed59e53b551213eac4e4578b",
        "backend/tests/raw_archive/test_minio_legacy.py": "502b74625c5a04e06348600cc38947743a3741d3f71aba3315c76cbf722f1b91",
    }
    for name, digest in expected.items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest
    image = subprocess.check_output(
        ["docker", "inspect", "--format", "{{.Image}}", "greenminddb-minio-1"],
        text=True,
        timeout=5,
    ).strip()
    assert image == IMAGE
    alias = json.loads(ALIAS.read_text())["aliases"]["greenmind-operator"]
    assert alias["url"] == "http://127.0.0.1:9000"
    client = boto3.client(
        "s3",
        endpoint_url=alias["url"],
        aws_access_key_id=alias["accessKey"],
        aws_secret_access_key=alias["secretKey"],
        config=Config(
            connect_timeout=3,
            read_timeout=8,
            retries={"total_max_attempts": 1},
            s3={"addressing_style": "path"},
        ),
    )
    before = protected()
    report = {
        "at": datetime.now(UTC).isoformat(),
        "deleted_files": 0,
        "operator_alias": str(ALIAS.parent),
        "contract_proof_date": "2026-10-03",
        "contract_code_unchanged": True,
        "minio_image": IMAGE,
        "legacy_null_deletion_enabled": False,
        "enable_requested": args.enable,
    }
    try:
        selected = json.loads((ROOT / "readback-selection.json").read_text())[
            "selected"
        ]
        heads = {}
        for kind, record in selected.items():
            assert record["bucket"] in BUCKETS
            h = client.head_object(Bucket=record["bucket"], Key=record["key"])
            heads[kind] = head_identity(h)
        if args.enable:
            report.update(enable_reviewed(client, checkpoint=guard))
        else:
            report["buckets"] = [inspected(client, bucket) for bucket in BUCKETS]
        for kind, record in selected.items():
            h = client.head_object(Bucket=record["bucket"], Key=record["key"])
            assert heads[kind] == head_identity(h)
        guard()
        assert protected() == before
        report.update(
            status="passed",
            sample_original_heads_unchanged=True,
            receivers_unchanged=True,
        )
        return 0
    except Exception as error:
        # A partially enabled bucket is protective. Never suspend or remove versions.
        report.update(
            status="blocked",
            error_type=type(error).__name__,
            partial_change_possible=args.enable,
        )
        return 1
    finally:
        client.close()
        path = ROOT / (
            "versioning-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + ".json"
        )
        with path.open("x") as body:
            os.fchmod(body.fileno(), 0o600)
            json.dump(report, body, indent=2)
        print(json.dumps(report))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "paused_before_change",
                    "error_type": type(error).__name__,
                    "deleted_files": 0,
                }
            )
        )
        raise SystemExit(75)
