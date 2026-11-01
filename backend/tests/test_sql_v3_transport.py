"""V3 transport contract tests use synthetic identities and do not grant access."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import codec
from app.tiered_archive import sql_deletion_v3 as v3
from app.tiered_archive.source import Source
from app.tiered_archive.sql_deletion_guard import ProductionGuardV3


def test_factory_binds_existing_authorization_to_current_lease():
    candidate = SimpleNamespace(identity='a' * 64, organization='org')
    permit = {'pilot_id': 'b' * 32, 'candidate': candidate.identity, 'runtime_sha256': 'c' * 64,
              'bindings_sha256': 'd' * 64, 'proof_contract_sha256': 'e' * 64, 'fence_contract_sha256': 'f' * 64,
              'audit': {'schema_sha256': '1' * 64, 'epoch': '2' * 32, 'slot': 4, 'pk_sha256': '3' * 64,
                        'source_row_sha256': ['4' * 64]}}
    result = v3.make_source_permit(candidate, permit, '5' * 64, {'database': 'fixture'},
                                   {'role': 'writer', 'role_oid': 42}, '6' * 32,
                                   producer_sha256='7' * 64, seconds=20)
    assert result['lease_session'] == '6' * 32 and result['producer_sha256'] == '7' * 64
    assert result['candidate_sha'] == candidate.identity and result['permit_sha'] == '5' * 64
    assert result['expected'] == ['4' * 64] and result['writer_oid'] == 42
    assert 0 < (datetime.fromisoformat(result['expires_at']) - datetime.now(UTC)).total_seconds() <= 20
    with pytest.raises(ArchiveBlocked, match='session'):
        v3.make_source_permit(candidate, permit, '5' * 64, {'database': 'fixture'}, {'role': 'writer', 'role_oid': 42}, 'wrong', producer_sha256='7' * 64, seconds=20)
    with pytest.raises(ArchiveBlocked, match='lifetime'):
        v3.make_source_permit(candidate, permit, '5' * 64, {'database': 'fixture'}, {'role': 'writer', 'role_oid': 42}, '6' * 32, producer_sha256='7' * 64, seconds=36)


def test_source_approval_default_is_blocked_before_io(monkeypatch):
    monkeypatch.delenv('TIERED_ARCHIVE_SQL_V3_SOURCE_APPROVAL_ENABLED', raising=False)
    with pytest.raises(ArchiveBlocked, match='controller required'):
        v3.stage_source_approval(None, None, None, authorization_path=None, authorization_sha256=None,
                                 runtime_sha256=None, bindings_sha256=None, proof_guard=None, checkpoint=lambda: None,
                                 clock=lambda: datetime.now(UTC), binding_path=None, binding_sha256=None,
                                 dba_identity_path=None, dba_identity_sha256=None, producer_sha256=None,
                                 challenge_file=None, frame_file=None)


def test_missing_native_restore_blocks_before_challenge_or_intent(monkeypatch, tmp_path):
    import app.raw_archive.remote_lease as lease
    import app.tiered_archive.policy as policy

    monkeypatch.setenv('TIERED_ARCHIVE_SQL_V3_SOURCE_APPROVAL_ENABLED', 'true')
    monkeypatch.setattr(v3.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(v3, 'bound_job_lock', lambda: None)
    monkeypatch.setattr(v3, 'require_bound_source_lease', lambda: None)
    monkeypatch.setattr(lease, 'require_bound_source_lease', lambda: None)
    monkeypatch.setattr(v3.sql_deletion, '_scope', lambda *_: None)
    monkeypatch.setattr(policy, 'eligibility', lambda *_: None)
    adapter = object.__new__(Source)
    adapter.expected = [{'body': {'row': {'measurement_id': 7}}}]
    candidate = SimpleNamespace(identity='a' * 64, dataset='ingest_log')
    permit = {'runtime_sha256': 'b' * 64, 'bindings_sha256': 'c' * 64,
              'audit': {'schema_sha256': 'd' * 64, 'epoch': 'e' * 32,
                        'genesis_sha256': 'f' * 64, 'pk_sha256': codec.digest([{'measurement_id': 7}])}}
    monkeypatch.setattr(v3, 'activation', lambda *_: permit)
    guard = object.__new__(ProductionGuardV3)
    guard.config = {'audit_schema_sha256': 'd' * 64, 'audit_v3_epoch': 'e' * 32, 'audit_v3_genesis_sha256': 'f' * 64}
    guard.candidate = candidate
    guard.checkpoint = lambda: None
    ledger = SimpleNamespace(root=tmp_path, get=lambda _identity: {})
    with pytest.raises(ArchiveBlocked, match='restore provenance unavailable'):
        v3.stage_source_approval(candidate, adapter, ledger, authorization_path=None, authorization_sha256='1' * 64,
                                 runtime_sha256='b' * 64, bindings_sha256='c' * 64, proof_guard=guard,
                                 checkpoint=lambda: None, clock=lambda: datetime.now(UTC), binding_path=None,
                                 binding_sha256=None, dba_identity_path=None, dba_identity_sha256=None,
                                 producer_sha256=None, challenge_file=None, frame_file=None)
    assert list(tmp_path.iterdir()) == []


def test_restore_guard_rejects_wrong_backup_manifest_before_network(monkeypatch):
    import app.tiered_archive.backup as backup
    import app.tiered_archive.sql_deletion_guard as guard_module
    import app.tiered_archive.sql_runtime as sql_runtime

    monkeypatch.setattr(v3.os, 'geteuid', lambda: 0)
    candidate = SimpleNamespace(identity='a' * 64, environment='production')
    guard = object.__new__(ProductionGuardV3)
    guard.candidate = candidate
    guard.config = {'audit_v3_restore_evidence_path': '/fixture/evidence', 'audit_v3_restore_evidence_sha256': 'b' * 64,
                    'audit_v3_backup_candidate': 'c' * 64, 'audit_v3_backup_manifest_sha256': 'd' * 64,
                    'source_identity': {'database': 'plantdb'}}
    evidence = {'format': 'greenmind.sql-audit-v3-native-restore-input.v1', 'backup_candidate': 'c' * 64,
                'backup_manifest_sha256': 'd' * 64, 'key_file': '/fixture/key', 'key_sha256': 'e' * 64,
                'target_profile_file': '/fixture/target', 'target_profile_sha256': 'f' * 64,
                'target': {}, 'target_writer_role': 'writer', 'provider_proof_file': '/fixture/provider',
                'provider_proof_sha256': '1' * 64, 'provider_terminal_file': '/fixture/wait', 'provider_terminal_sha256': '2' * 64}
    monkeypatch.setattr(guard_module, '_private_json', lambda *_: evidence)
    monkeypatch.setattr(sql_runtime, '_private_file', lambda path, *_: b'k' * 32 if path.endswith('/key') else b'postgresql://writer@localhost/plant_restore_test')
    monkeypatch.setattr(backup, 'key_from_file', lambda *_: b'k' * 32)
    guard.store = SimpleNamespace(identity='destination')
    guard.ledger = SimpleNamespace(get=lambda _: {'state': 'verified', 'destination': 'destination', 'manifest': {'sha256': '0' * 64}})
    with pytest.raises(ArchiveBlocked, match='verified V3 Gateway backup'):
        v3.require_native_restore_evidence(guard, candidate, checkpoint=lambda: None)
