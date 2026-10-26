"""Never prepare operator credentials or a broadened source-copy policy for transfer."""

import importlib.util
import json
from pathlib import Path

import pytest


def helper():
    path = (
        Path(__file__).resolve().parents[3] / "deploy/archive-server/prepare-copy-source-access.py"
    )
    spec = importlib.util.spec_from_file_location("source_access", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def policy():
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                "Resource": [
                    "arn:aws:s3:::greenmind-raw/*",
                    "arn:aws:s3:::greenmind-direct-production-hotspot/*",
                ],
            },
            {
                "Effect": "Deny",
                "Action": [
                    "s3:DeleteObjectVersion",
                    "s3:PutLifecycleConfiguration",
                    "s3:PutObject",
                    "s3:DeleteBucket",
                    "s3:DeleteObject",
                ],
                "Resource": ["arn:aws:s3:::*"],
            },
        ],
    }


@pytest.mark.parametrize("change", ["put", "delete", "other_bucket", "condition", "admin"])
def test_privilege_drift_blocks_preparation(change):
    value = policy()
    if change in ("put", "delete"):
        value["Statement"][0]["Action"].append(
            "s3:PutObject" if change == "put" else "s3:DeleteObject"
        )
    elif change == "other_bucket":
        value["Statement"][0]["Resource"].append("arn:aws:s3:::other/*")
    elif change == "condition":
        value["Statement"][0]["Condition"] = {}
    else:
        value["Statement"].append({"Effect": "Allow", "Action": ["admin:*"], "Resource": ["*"]})
    with pytest.raises(RuntimeError, match="Unreviewed"):
        helper().verify_policy(value)


def test_preflight_policy_is_separate_read_only_and_not_a_removal_permission():
    path = (
        Path(__file__).resolve().parents[3]
        / "deploy/raw-archive/delete-preparation/preflight-read-policy.production.json"
    )
    value = json.loads(path.read_text())
    allowed = {a for s in value["Statement"] if s["Effect"] == "Allow" for a in s["Action"]}
    assert "s3:ListBucketVersions" in allowed and "s3:GetObjectVersion" in allowed
    assert not {"s3:DeleteObjectVersion", "s3:DeleteObject", "admin:*"} & allowed
    denied = {a for s in value["Statement"] if s["Effect"] == "Deny" for a in s["Action"]}
    assert {"s3:DeleteObjectVersion", "s3:PutObject", "s3:PutBucketVersioning"} <= denied
