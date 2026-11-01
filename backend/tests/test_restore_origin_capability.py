"""Explicit origin-only admission; no live PostgreSQL or ACL modification."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import restore_fence as fence
from app.tiered_archive import restore_origin_capability as cap
from tests.restore_fence_sqlite import target_sha256
from tests.test_restore_fence import DOMAIN

pytest_plugins = ["tests.test_restore_fence"]


def role(oid, **changes):
    value = {
        "oid": oid,
        "name": f"role_{oid}",
        "login": True,
        "superuser": False,
        "inherit": False,
        "create_role": False,
        "create_db": False,
        "replication": False,
        "bypass_rls": False,
        "parameter_set": False,
        "parameter_alter_system": False,
        "connect": False,
        "temp": False,
        "member_roles": [oid],
    }
    value.update(changes)
    return value


def facts():
    return {
        "database": {
            "oid": 100,
            "actor": 1,
            "session": 1,
            "owner": 1,
            "version": 150010,
            "recovery": False,
            "origin": "origin",
            "public_connect": False,
            "public_temp": False,
            "acl": "private",
        },
        "roles": [
            role(1, connect=True, temp=True),
            role(2, superuser=True),
            role(3),
            role(10, superuser=True),
        ],
        "memberships": [],
        "parameter_acl": [],
        "functions": [],
        "extensions": [{"oid": 900, "name": "timescaledb", "version": "2.17.2", "owner": 1}],
        "other_clients": False,
        "subscriptions": False,
        "scheduled_jobs": False,
        "events": events(),
        "scope_acl_safe": True,
    }


def function(owner=3, **changes):
    value = {
        "oid": 1000,
        "owner": owner,
        "definer": True,
        "sensitive_config": False,
        "execute_roles": [1],
        "trigger_invoked": False,
        "catalog_sha256": "a" * 64,
    }
    value.update(changes)
    return value


def test_explicit_unprivileged_private_owner_capability_is_stable():
    value = facts()
    proof = cap.validate(value)
    assert proof["sha256"] == cap.validate(deepcopy(value))["sha256"]
    assert proof["authority"]["mode"] == cap.MODE
    assert "scope_acl_safe" not in proof["authority"]  # Dynamic new own objects aren't fixed OIDs.


def events():
    common = {
        "owner": 10,
        "function_owner": 10,
        "enabled": "O",
        "function_oid": 1001,
        "extension_oid": 900,
        "function_extension_oid": 900,
        "function_name": "process_ddl_event",
        "function_schema": "_timescaledb_functions",
        "language": "c",
        "binary": "$libdir/timescaledb-2.17.2",
        "symbol": "ts_timescaledb_process_ddl_event",
        "args": 0,
        "plain_signature": True,
        "definer": False,
        "config": None,
        "acl": None,
        "returns_event_trigger": True,
        "returns_set": False,
        "strict": False,
        "leakproof": False,
        "volatility": "v",
        "parallel": "u",
        "kind": "f",
    }
    return [
        {
            **common,
            "oid": 2001,
            "name": "timescaledb_ddl_command_end",
            "event": "ddl_command_end",
            "tags": sorted(cap._DDL_TAGS),
        },
        {
            **common,
            "oid": 2002,
            "name": "timescaledb_ddl_sql_drop",
            "event": "sql_drop",
            "tags": None,
        },
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("superuser", True),
        ("create_role", True),
        ("create_db", True),
        ("replication", True),
        ("bypass_rls", True),
        ("parameter_set", True),
        ("parameter_alter_system", True),
        ("name", "pg_execute_server_program"),
    ],
)
def test_owner_and_every_set_role_authority_must_remain_unprivileged(field, value):
    data = facts()
    data["roles"][0][field] = value
    with pytest.raises(ArchiveBlocked, match="reachable privileged"):
        cap.validate(data)


def test_noinherit_hierarchical_set_role_is_not_hidden_by_usages():
    data = facts()
    data["roles"][0]["member_roles"] = [1, 3]
    data["roles"][2]["member_roles"] = [3, 4]
    data["roles"].append(role(4, login=False, parameter_set=True))
    data["memberships"] = [{"member": 1, "role": 3}, {"member": 3, "role": 4}]
    with pytest.raises(ArchiveBlocked, match="reachable privileged"):
        cap.validate(data)


def test_implicit_pg_database_owner_member_needs_no_physical_catalog_edge():
    data = facts()
    data["roles"].append(role(6171, name="pg_database_owner", login=False, parameter_set=True))
    data["roles"][0]["member_roles"] = [1, 6171]
    assert data["memberships"] == []
    with pytest.raises(ArchiveBlocked, match="reachable privileged"):
        cap.validate(data)


def test_another_owner_capable_login_cannot_supply_a_replica_escape():
    data = facts()
    data["roles"][2]["member_roles"] = [3, 1]
    data["roles"][2]["parameter_set"] = True
    with pytest.raises(ArchiveBlocked, match="reachable privileged"):
        cap.validate(data)


@pytest.mark.parametrize(
    "kind",
    [
        "public_connect",
        "public_temp",
        "foreign_connect",
        "foreign_setrole_temp",
        "client",
        "subscription",
        "jobs",
        "events",
        "column_acl",
        "owner_acl",
    ],
)
def test_no_extra_client_replication_background_or_writer_scope(kind):
    data = facts()
    if kind in {"public_connect", "public_temp"}:
        data["database"][kind] = True
    elif kind == "foreign_connect":
        data["roles"][2]["connect"] = True
    elif kind == "foreign_setrole_temp":
        data["roles"].append(role(4, login=False, temp=True))
        data["roles"][2]["member_roles"] = [3, 4]
    elif kind in {"column_acl", "owner_acl"}:
        data["scope_acl_safe"] = False
    elif kind == "events":
        data["events"].append({**data["events"][0], "oid": 999, "name": "extra"})
    else:
        data[
            {
                "client": "other_clients",
                "subscription": "subscriptions",
                "jobs": "scheduled_jobs",
            }[kind]
        ] = True
    with pytest.raises(ArchiveBlocked):
        cap.validate(data)


@pytest.mark.parametrize(
    "change",
    [{"actor": 2}, {"session": 2}, {"origin": "replica"}, {"version": 160000}, {"recovery": True}],
)
def test_actor_identity_origin_and_supported_primary_are_explicit(change):
    data = facts()
    data["database"].update(change)
    with pytest.raises(ArchiveBlocked):
        cap.validate(data)


@pytest.mark.parametrize("trigger", [False, True])
def test_callable_or_trigger_invoked_privileged_definer_is_not_extension_trusted(trigger):
    data = facts()
    data["functions"] = [
        function(owner=2, execute_roles=[] if trigger else [1], trigger_invoked=trigger)
    ]
    with pytest.raises(ArchiveBlocked, match="reachable privileged"):
        cap.validate(data)


def test_definer_chain_follows_its_owner_member_closure_too():
    data = facts()
    data["roles"].append(role(4, login=False, parameter_set=True))
    data["roles"][2]["member_roles"] = [3, 4]
    data["functions"] = [function(owner=3)]
    with pytest.raises(ArchiveBlocked, match="reachable privileged"):
        cap.validate(data)


def test_function_replica_config_cannot_restore_origin_and_hide_mutation():
    data = facts()
    data["functions"] = [function(owner=1, sensitive_config=True)]
    with pytest.raises(ArchiveBlocked, match="GUC override"):
        cap.validate(data)


def test_authority_sha_binds_role_membership_parameter_and_function_acl():
    original = facts()
    proof = cap.validate(original)
    changed = deepcopy(original)
    changed["parameter_acl"] = [
        {"oid": 12, "parname": "session_replication_role", "acl": "safe-new-grantor"}
    ]
    assert cap.validate(changed)["sha256"] != proof["sha256"]
    changed = deepcopy(original)
    changed["functions"] = [function(owner=1)]
    assert cap.validate(changed)["sha256"] != proof["sha256"]


def test_mode_never_silently_falls_back_from_always_or_accepts_unpinned_origin():
    domain = {"environment": "production", "source": "gateway", "schema_master_sha256": "a" * 64}
    assert fence._domain_mode(domain) == (fence.ALWAYS, None)
    with pytest.raises(ArchiveBlocked):
        fence._domain_mode({**domain, "fence_mode": cap.MODE})
    with pytest.raises(ArchiveBlocked):
        fence._domain_mode(
            {**domain, "fence_mode": fence.ALWAYS, "fence_capability_sha256": "a" * 64}
        )
    assert fence._domain_mode(
        {**domain, "fence_mode": cap.MODE, "fence_capability_sha256": "b" * 64}
    ) == (cap.MODE, "b" * 64)


def test_origin_mode_installs_only_supported_normal_triggers_without_any_alter(monkeypatch):
    statements = []
    monkeypatch.setattr(fence, "_trigger", lambda *_: None)
    db = SimpleNamespace(execute=lambda clause: statements.append(str(clause)))
    fence._install_triggers(db, "samples", mode=cap.MODE)
    assert len(statements) == 2 and all("CREATE TRIGGER" in s for s in statements)
    assert not any("ALTER TABLE" in s for s in statements)
    assert any("TRUNCATE" in s for s in statements)


def test_trigger_enabled_state_is_mode_specific_and_not_interchangeable():
    row = {
        "enabled": "O",
        "kind": 29,
        "function": True,
        "nargs": 1,
        "args": b"samples\x00",
        "unconditional": True,
    }
    assert fence._valid_trigger(row, "samples", 29, mode=cap.MODE)
    assert not fence._valid_trigger(row, "samples", 29)
    row["enabled"] = "A"
    assert not fence._valid_trigger(row, "samples", 29, mode=cap.MODE)


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner", 1),
        ("function_owner", 1),
        ("enabled", "D"),
        ("event", "ddl_command_start"),
        ("tags", ["ALTER TABLE"]),
        ("extension_oid", 999),
        ("function_extension_oid", 999),
        ("function_name", "wrapper"),
        ("language", "plpgsql"),
        ("binary", "$libdir/unreviewed"),
        ("symbol", "different_entry"),
        ("definer", True),
        ("config", ["session_replication_role=replica"]),
        ("acl", "public-changed"),
        ("plain_signature", False),
    ],
)
def test_only_exact_reviewed_timescale_provider_events_are_admissible(field, value):
    data = facts()
    data["events"][0][field] = value
    with pytest.raises(ArchiveBlocked, match="event trigger provenance"):
        cap.validate(data)


def test_provider_extension_version_and_catalog_pins_are_not_blanket_trust():
    data = facts()
    before = cap.validate(data)
    data["events"][0]["full_function"] = {"new_catalog_attribute": "changed"}
    assert cap.validate(data)["sha256"] != before["sha256"]
    data["extensions"][0]["version"] = "2.18.0"
    with pytest.raises(ArchiveBlocked):
        cap.validate(data)


def test_product_guard_never_mutates_privileges_or_subscriptions():
    import inspect

    source = inspect.getsource(cap.capture)
    assert "pg_catalog.pg_subscription" in source and "subconninfo" not in source
    assert "pg_catalog.pg_has_role" in source and "'MEMBER'" in source
    assert "pg_catalog.has_parameter_privilege" in source
    assert "GRANT " not in source and "REVOKE " not in source and "SET ROLE" not in source


def test_mode_and_capability_pin_survive_resume_and_recheck_each_fence_call(target, monkeypatch):
    db, tables = target
    checks = []
    current = ["a" * 64]

    def verify(_db, expected):
        checks.append(expected)
        if current[0] != expected:
            raise ArchiveBlocked("Origin capability authority changed")

    monkeypatch.setattr(cap, "verify", verify)
    domain = {**DOMAIN, "fence_mode": cap.MODE, "fence_capability_sha256": current[0]}
    snapshot = fence.ensure(db, tables, {"devices"}, target_sha256(db), domain)
    assert fence.check(db) == snapshot
    assert len(checks) >= 3
    state, _ = fence._control_tables()
    saved = db.execute(state.select()).mappings().one()
    assert saved["mode"] == cap.MODE and saved["capability_sha256"] == current[0]
    current[0] = "b" * 64
    with pytest.raises(ArchiveBlocked, match="authority changed"):
        fence.check(db)


def test_control_mode_bit_cannot_skip_its_original_domain_admission(target, monkeypatch):
    db, tables = target
    monkeypatch.setattr(cap, "verify", lambda *_: None)
    domain = {**DOMAIN, "fence_mode": cap.MODE, "fence_capability_sha256": "a" * 64}
    fence.ensure(db, tables, {"devices"}, target_sha256(db), domain)
    state, _ = fence._control_tables()
    db.execute(state.update().values(mode=fence.ALWAYS, capability_sha256=None))
    with pytest.raises(ArchiveBlocked, match="mode/domain binding changed"):
        fence.check(db)
