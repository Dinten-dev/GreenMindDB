from types import SimpleNamespace

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.services.signal_feature_contract import canonical_sha256
from app.tiered_archive.runtime import bind_direct_scientific_proof


def fixture():
    snapshot = {
        "provider_id": 1,
        "box_id": 2,
        "name": "fixture-snapshot",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    proof = {"provider_snapshot": snapshot, "history_sha256": "a" * 64}
    proofs = {"fixture": proof}
    permit = {
        "direct_scientific_proofs": proofs,
        "acceptance": {"direct_scientific_proofs_sha256": canonical_sha256(proofs)},
    }
    calls = []
    adapter = SimpleNamespace(
        include_feature_history=True, install_scientific_proof=lambda *args: calls.append(args)
    )
    return (
        adapter,
        SimpleNamespace(source="direct", identity="fixture"),
        {"snapshot": {**snapshot, "checked_at": "2026-01-02T00:00:00+00:00"}},
        permit,
        SimpleNamespace(),
        calls,
    )


def test_bound_snapshot_renewal_does_not_change_scientific_identity():
    adapter, candidate, receipt, permit, store, calls = fixture()
    bind_direct_scientific_proof(adapter, candidate, receipt, permit, store)
    receipt["snapshot"]["checked_at"] = "2026-01-03T00:00:00+00:00"
    bind_direct_scientific_proof(adapter, candidate, receipt, permit, store)
    assert calls[0] == calls[1]
    assert calls[0][2] is store


@pytest.mark.parametrize("change", ["missing", "tampered", "foreign_snapshot", "no_history"])
def test_unaccepted_or_rebound_proofs_fail_before_adapter(change):
    adapter, candidate, receipt, permit, store, calls = fixture()
    if change == "missing":
        permit.pop("direct_scientific_proofs")
    elif change == "tampered":
        permit["direct_scientific_proofs"]["fixture"]["history_sha256"] = "b" * 64
    elif change == "foreign_snapshot":
        receipt["snapshot"]["provider_id"] = 3
    else:
        adapter.include_feature_history = False
    with pytest.raises(ArchiveBlocked):
        bind_direct_scientific_proof(adapter, candidate, receipt, permit, store)
    assert not calls
