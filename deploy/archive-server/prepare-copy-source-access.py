"""Prepare existing object-read access only; no identity, policy, writer or transfer."""

import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

SOURCE = Path("/etc/greenmind/raw-copy/storagebox.env")
OPERATOR = Path("/home/traver/.config/greenmind/minio-operator-a3b88514f95551f1")
MC = "/home/traver/greenmind-operator-preparation-20261005-1055/mc"
ROOT = Path("/etc/greenmind/source-read-access-preparation-20261009")
BUCKETS = {"greenmind-raw", "greenmind-direct-production-hotspot"}


def verify_policy(policy):
    statements = policy.get("Statement", [])
    if len(statements) != 2:
        raise RuntimeError("Unreviewed source-read policy")
    allowed = [s for s in statements if s.get("Effect") == "Allow"]
    denied = [s for s in statements if s.get("Effect") == "Deny"]
    if len(allowed) != 1 or len(denied) != 1:
        raise RuntimeError("Unreviewed source-read policy")
    expected = {"arn:aws:s3:::" + name + "/*" for name in BUCKETS}
    if (
        set(allowed[0].get("Action", [])) != {"s3:GetObject", "s3:GetObjectVersion"}
        or set(allowed[0].get("Resource", [])) != expected
        or set(denied[0].get("Action", []))
        != {
            "s3:DeleteObjectVersion",
            "s3:PutLifecycleConfiguration",
            "s3:PutObject",
            "s3:DeleteBucket",
            "s3:DeleteObject",
        }
        or denied[0].get("Resource") != ["arn:aws:s3:::*"]
        or any(set(s) != {"Effect", "Action", "Resource"} for s in statements)
    ):
        raise RuntimeError("Unreviewed source-read policy")


def query(*args):
    # The existing non-secret principal identifier is an argument; passwords are not.
    process = subprocess.run(
        [MC, "--config-dir", str(OPERATOR), "--json", "admin", *args],
        capture_output=True,
        text=True,
        timeout=8,
    )
    if process.returncode or len(process.stdout) > 1024**2:
        raise RuntimeError("Operator read query refused")
    return json.loads(process.stdout)


def main():
    assert os.geteuid() == 0 and os.uname().nodename == "Biolingo"
    assert not ROOT.exists(), "Preserve an earlier prepared access package"
    assert (
        SOURCE.is_file()
        and not SOURCE.is_symlink()
        and not SOURCE.stat().st_mode & 0o007
    )
    values = dict(
        line.split("=", 1)
        for line in SOURCE.read_text().splitlines()
        if line and not line.startswith("#")
    )
    operator = json.loads((OPERATOR / "config.json").read_text())["aliases"][
        "greenmind-operator"
    ]
    selected = {}
    for kind in ("GATEWAY", "DIRECT"):
        for field in ("ENDPOINT", "ACCESS_KEY", "SECRET_KEY"):
            key = "RAW_ARCHIVE_" + kind + "_S3_" + field
            assert values.get(key) and "\n" not in values[key]
            selected[key] = values[key]
        assert (
            selected["RAW_ARCHIVE_" + kind + "_S3_ENDPOINT"] == "http://127.0.0.1:9000"
        )
        account = selected["RAW_ARCHIVE_" + kind + "_S3_ACCESS_KEY"]
        assert account != operator["accessKey"], "Never export the operator identity"
        info = query("user", "info", "greenmind-operator", account)
        assert (
            info.get("status") == "success"
            and info.get("policyName") == "greenmind-raw-copy"
        )
    policy = query("policy", "info", "greenmind-operator", "greenmind-raw-copy")[
        "policyInfo"
    ]
    policy = json.loads(policy) if isinstance(policy, str) else policy
    verify_policy(policy["Policy"])
    ROOT.mkdir(mode=0o700)
    proof = {
        "source_policy": policy["Policy"],
        "destination_host": "2.31.22.33",
        "object_read_only": True,
        "operator_identity_excluded": True,
        "no_new_identity": True,
        "transferred": False,
        "deleted_files": 0,
        "list_versions_permission": False,
        "deletion_permission": False,
    }
    contents = {
        "source-read.env": "".join(
            k + "=" + v + "\n" for k, v in sorted(selected.items())
        ).encode(),
        "source-policy-proof.json": json.dumps(proof, sort_keys=True).encode(),
    }
    target = ROOT / "source-read-access.tar"
    with target.open("xb") as body:
        os.fchmod(body.fileno(), 0o600)
        with tarfile.open(fileobj=body, mode="w") as archive:
            for name, data in contents.items():
                entry = tarfile.TarInfo(name)
                entry.mode = 0o600
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        body.flush()
        os.fsync(body.fileno())
    print(
        json.dumps(
            {
                "package": str(target),
                "bytes": target.stat().st_size,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "source_read_only": True,
                "transferred": False,
                "new_administrators": 0,
                "deleted_files": 0,
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
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
