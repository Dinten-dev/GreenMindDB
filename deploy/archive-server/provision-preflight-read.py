"""Create one expiring scoped read key through the regular operator alias only."""

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

MC = "/home/traver/greenmind-operator-preparation-20261005-1055/mc"
OPERATOR = Path("/home/traver/.config/greenmind/minio-operator-a3b88514f95551f1")


def friendly_name(stamp):
    return "gm-preflight-" + stamp[:15]


def denied_admin(result):
    # Deployed mc emits status=error with exit 0 for per-server admin-info denial.
    # The structured response is authoritative; an arbitrary CLI failure is not.
    data = json.loads(result.stdout)
    if data.get("status") != "error":
        return False
    if data.get("error") in ("Access Denied.", "Access Denied", "AccessDenied"):
        return True

    def reasons(value):
        if isinstance(value, dict):
            found = {str(value[k]) for k in ("Code", "message") if k in value}
            for child in value.values():
                found.update(reasons(child))
            return found
        if isinstance(value, list):
            return set().union(*(reasons(child) for child in value))
        return set()

    return bool(
        reasons(data)
        & {"AccessDenied", "XMinioAdminAccessDenied", "Access Denied.", "Access Denied"}
    )


def validate(policy):
    buckets = {
        "arn:aws:s3:::greenmind-raw",
        "arn:aws:s3:::greenmind-direct-production-hotspot",
    }
    if set(policy) != {"Version", "Statement"} or policy["Version"] != "2012-10-17":
        raise ValueError("Invalid policy")
    rules = policy["Statement"]
    expected = [
        ("Allow", {"s3:GetObject", "s3:GetObjectVersion"}, {x + "/*" for x in buckets}),
        (
            "Allow",
            {
                "s3:ListBucketVersions",
                "s3:GetBucketVersioning",
                "s3:GetLifecycleConfiguration",
            },
            buckets,
        ),
        (
            "Deny",
            {
                "s3:PutObject",
                "s3:DeleteObject",
                "s3:DeleteObjectVersion",
                "s3:DeleteBucket",
                "s3:PutBucketVersioning",
                "s3:PutLifecycleConfiguration",
            },
            {"arn:aws:s3:::*"},
        ),
    ]
    if len(rules) != 3 or any(
        set(x) != {"Effect", "Action", "Resource"} for x in rules
    ):
        raise ValueError("Invalid policy rules")
    actual = [(x["Effect"], set(x["Action"]), set(x["Resource"])) for x in rules]
    if any(x not in actual for x in expected):
        raise ValueError("Broadened policy refused")


def private(path, value):
    with path.open("xb") as body:
        os.fchmod(body.fileno(), 0o600)
        body.write(value)
        body.flush()
        os.fsync(body.fileno())


def main():
    assert os.geteuid() == 0 and os.uname().nodename == "Biolingo"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()
    runtime = Path(__file__).resolve().parents[2]
    for name, sha in json.loads((runtime / "files.json").read_text()).items():
        assert hashlib.sha256((runtime / name).read_bytes()).hexdigest() == sha
    policy_path = (
        runtime
        / "deploy/raw-archive/delete-preparation/preflight-read-policy.production.json"
    )
    policy = json.loads(policy_path.read_text())
    validate(policy)
    # Never inspect a container environment or discover an operator password.
    operator = json.loads((OPERATOR / "config.json").read_text())["aliases"][
        "greenmind-operator"
    ]
    assert operator["url"] == "http://127.0.0.1:9000"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    root = args.resume or Path("/etc/greenmind") / ("preflight-read-" + stamp)
    if args.resume:
        assert root.parent == Path("/etc/greenmind") and root.name.startswith(
            "preflight-read-20261010T"
        )
        assert (
            root.resolve() == root
            and root.stat().st_uid == 0
            and not root.stat().st_mode & 0o077
        )
        assert not (root / "proof.json").exists()
    else:
        root.mkdir(mode=0o700)
    if not args.resume:
        result = subprocess.run(
            [
                MC,
                "--disable-pager",
                "--config-dir",
                str(OPERATOR),
                "--json",
                "admin",
                "accesskey",
                "create",
                "--policy",
                str(policy_path),
                "--name",
                friendly_name(stamp),
                "--description",
                "No uploads, deletion, versioning changes or admin operations",
                "--expiry-duration",
                "168h",
                "greenmind-operator",
            ],
            check=False,
            capture_output=True,
            timeout=15,
        )
        # Preserve even an ambiguous response; NEVER repeat creation automatically.
        private(root / "creation-response.json", result.stdout)
        if result.returncode or len(result.stdout) > 65536:
            raise RuntimeError("Scoped creation did not return confirmed success")
    created = root / "creation-response.json"
    assert (
        not created.is_symlink()
        and created.stat().st_uid == 0
        and not created.stat().st_mode & 0o077
    )
    assert created.stat().st_size <= 65536
    value = json.loads(created.read_bytes())
    assert value.get("status") == "success"
    assert value.get("accessKey") and value.get("secretKey")
    assert value["accessKey"] != operator["accessKey"]
    actual = subprocess.run(
        [
            MC,
            "--disable-pager",
            "--config-dir",
            str(OPERATOR),
            "--json",
            "admin",
            "accesskey",
            "info",
            "greenmind-operator",
            value["accessKey"],
        ],
        check=False,
        capture_output=True,
        timeout=8,
    )
    private(root / ("actual-scope-" + stamp + ".json"), actual.stdout)
    assert actual.returncode == 0 and len(actual.stdout) <= 65536
    actual_policy = json.loads(actual.stdout)
    assert (
        actual_policy.get("status") == "success"
        and actual_policy.get("accountStatus") == "on"
    )
    validate(actual_policy["policy"])
    expiration = datetime.fromisoformat(actual_policy["expiration"])
    assert (
        datetime.now(UTC).timestamp()
        < expiration.timestamp()
        <= datetime.now(UTC).timestamp() + 168 * 3600 + 60
    )
    alias = {
        key: val
        for key, val in operator.items()
        if key not in {"accessKey", "secretKey", "sessionToken"}
    }
    alias.update(accessKey=value["accessKey"], secretKey=value["secretKey"])
    config = {"version": "10", "aliases": {"greenmind-preflight": alias}}
    if args.resume:
        assert json.loads((root / "config.json").read_text()) == config
    else:
        private(root / "config.json", json.dumps(config).encode())
    negative = subprocess.run(
        [
            MC,
            "--disable-pager",
            "--config-dir",
            str(root),
            "--json",
            "admin",
            "info",
            "greenmind-preflight",
        ],
        check=False,
        capture_output=True,
        timeout=8,
    )
    private(root / ("admin-negative-response-" + stamp + ".json"), negative.stdout)
    # Successful admin info is unacceptable even if the creation requested a policy.
    assert denied_admin(negative)
    private(
        root / "read.env",
        (
            "RAW_ARCHIVE_DIAGNOSTIC_ENDPOINT=http://127.0.0.1:9000\n"
            "RAW_ARCHIVE_DIAGNOSTIC_ACCESS_KEY=" + value["accessKey"] + "\n"
            "RAW_ARCHIVE_DIAGNOSTIC_SECRET_KEY=" + value["secretKey"] + "\n"
            "RAW_ARCHIVE_DIRECT_S3_BUCKET=greenmind-direct-production-hotspot\n"
        ).encode(),
    )
    proof = {
        "created_at": datetime.now(UTC).isoformat(),
        "config": str(root),
        "identity": value["accessKey"],
        "expiry_duration_hours": 168,
        "policy_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        "admin_info_denied": True,
        "actual_attached_policy_verified": True,
        "expires_at": actual_policy["expiration"],
        "source_delete_allowed": False,
        "copier_policy_changed": False,
        "deleted_files": 0,
    }
    private(root / "proof.json", json.dumps(proof, sort_keys=True).encode())
    print(json.dumps(proof))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - credential-safe boundary
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_type": type(error).__name__,
                    "deleted_files": 0,
                }
            )
        )
        raise SystemExit(1) from None
