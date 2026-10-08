"""A candidate must not depend on ambiguous old-release application aliases."""

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.read_api_target import RECEIVER, dashboard_target


def test_candidate_uses_unique_preserved_receiver_name():
    value = dashboard_target(
        {"source": {"external": True, "name": "production-network"}},
        {"name": RECEIVER, "running": True, "networks": {"production-network": {}}},
    )
    assert value == f"http://{RECEIVER}:8000"


@pytest.mark.parametrize("damage", ["stopped", "wrong_receiver", "different_network"])
def test_unreachable_or_foreign_receiver_rejects_candidate(damage):
    target = {"name": RECEIVER, "running": True, "networks": ["production-network"]}
    if damage == "stopped":
        target["running"] = False
    elif damage == "wrong_receiver":
        target["name"] = "old-application"
    else:
        target["networks"] = ["staging-network"]
    with pytest.raises(ArchiveBlocked):
        dashboard_target({"production-network": {}}, target)
