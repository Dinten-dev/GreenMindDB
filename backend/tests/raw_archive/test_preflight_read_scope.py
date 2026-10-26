import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load():
    path = ROOT / "deploy/archive-server/provision-preflight-read.py"
    spec = importlib.util.spec_from_file_location("preflight_scope", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    policy = json.loads(
        (
            ROOT / "deploy/raw-archive/delete-preparation/preflight-read-policy.production.json"
        ).read_text()
    )
    return module, policy


def test_exact_policy_has_only_reviewed_reads():
    module, policy = load()
    module.validate(policy)


def test_service_name_fits_deployed_server_limit():
    module, _ = load()
    assert len(module.friendly_name("20261010T062303141156")) <= 32


def test_deployed_top_level_error_string_denial():
    module, _ = load()
    result = SimpleNamespace(
        returncode=1, stdout=json.dumps({"status": "error", "error": "Access Denied."})
    )
    assert module.denied_admin(result)


@pytest.mark.parametrize(
    "code,reason,accepted",
    [
        (1, "Access Denied.", True),
        (1, "AccessDenied", True),
        (1, "Unknown alias", False),
        (0, "Access Denied.", True),
    ],
)
def test_admin_negative_requires_real_access_denial(code, reason, accepted):
    module, _ = load()
    result = SimpleNamespace(
        returncode=code,
        stdout=json.dumps({"status": "error", "error": {"cause": {"message": reason}}}),
    )
    assert module.denied_admin(result) is accepted


def test_successful_admin_response_never_counts_as_denial():
    module, _ = load()
    assert not module.denied_admin(SimpleNamespace(returncode=0, stdout='{"status":"success"}'))


@pytest.mark.parametrize("drift", ["admin", "wildcard", "delete", "condition", "extra"])
def test_broadened_or_incomplete_scope_refused(drift):
    module, policy = load()
    if drift == "admin":
        policy["Statement"][0]["Action"].append("admin:*")
    if drift == "wildcard":
        policy["Statement"][0]["Resource"] = ["arn:aws:s3:::*"]
    if drift == "delete":
        policy["Statement"][0]["Action"].append("s3:DeleteObject")
    if drift == "condition":
        policy["Statement"][2]["Condition"] = {"ignored": True}
    if drift == "extra":
        policy["Statement"].append(policy["Statement"][0])
    with pytest.raises(ValueError):
        module.validate(policy)
