"""Offline TLS propagation tests; Root/Source fixtures are not native authority."""

import hashlib
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import database_restore, parent_companion, recovery

URL = (
    "postgresql+psycopg2://gm_probe_owner:unit-secret@127.0.0.1:19442/gm_native_restore_"
    + "a" * 32
    + "_gateway"
)


@pytest.fixture
def binding(tmp_path, monkeypatch):
    """Simulate Root filesystem metadata only, never native Source success."""
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    entries = {}
    raw = {}
    for name in ("source-proof", "ca.crt", "client.crt", "client.key"):
        path = private / name
        raw[name] = ("unit-only-" + name).encode()
        path.write_bytes(raw[name])
        path.chmod(0o400)
        info = path.stat()
        entries[name] = recovery.RestoreTLSFilePin(
            path,
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            hashlib.sha256(raw[name]).hexdigest(),
        )
    original_lstat, original_fstat = Path.lstat, os.fstat
    inodes = {pin.inode for pin in entries.values()}

    class RootInfo:
        def __init__(self, value):
            self.value = value

        def __getattr__(self, name):
            return 0 if name in {"st_uid", "st_gid"} else getattr(self.value, name)

    def lstat(path):
        value = original_lstat(path)
        return RootInfo(value) if path.is_relative_to(tmp_path) else value

    def fstat(descriptor):
        value = original_fstat(descriptor)
        return RootInfo(value) if value.st_ino in inodes else value

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(os, "fstat", fstat)
    monkeypatch.setattr(recovery, "_TLS_ROOT", tmp_path)
    monkeypatch.setattr(recovery, "_tls_archive_authority", lambda: None)
    target = recovery.RestoreTLSTarget(
        make_url(URL).database,
        41001,
        "gm_probe_owner",
        41002,
        19442,
        "127.0.0.1/32",
        "2026-11-02 01:00:00.123456+00",
        "b" * 64,
    )
    calls = []

    def guard(current, artifact):
        assert current.target == target and artifact == raw["source-proof"]
        calls.append(current.source_target_proof.sha256)

    value = recovery.RestoreTLSBinding(
        entries["source-proof"],
        entries["ca.crt"],
        entries["client.crt"],
        entries["client.key"],
        target,
        guard,
    )
    return value, calls


def test_none_preserves_default_without_root_files_or_new_environment(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Default restore must not require TLS authority")

    monkeypatch.setattr(recovery, "_tls_archive_authority", forbidden)

    def checkpoint():
        return None

    url = make_url("postgresql+psycopg2://user@127.0.0.1/isolated_restore_test")
    assert recovery.restore_tls_checkpoint(None, url, checkpoint) is checkpoint
    assert recovery.restore_tls_env(None, url) == {}
    assert recovery.restore_tls_connect_args(None, url) == {}
    assert recovery.restore_tls_guard_sql(None, url) == ""
    assert recovery.restore_tls_evidence(None) == {}


def test_explicit_exact_tls_env_and_engine_arguments_ignore_environment(binding, monkeypatch):
    value, calls = binding
    for name in ("PGSSLMODE", "PGSSLKEY", "PGHOSTADDR", "PGSERVICE"):
        monkeypatch.setenv(name, "untrusted")
    url = make_url(URL)
    expected = {
        "hostaddr": "127.0.0.1",
        "sslmode": "verify-full",
        "sslrootcert": str(value.root_certificate.path),
        "sslcert": str(value.client_certificate.path),
        "sslkey": str(value.client_key.path),
    }
    assert recovery.restore_tls_connect_args(value, url) == expected
    assert recovery.restore_tls_env(value, url) == {
        "PGHOSTADDR": expected["hostaddr"],
        "PGSSLMODE": "verify-full",
        "PGSSLROOTCERT": expected["sslrootcert"],
        "PGSSLCERT": expected["sslcert"],
        "PGSSLKEY": expected["sslkey"],
    }
    assert len(calls) == 2
    evidence = recovery.restore_tls_evidence(value, url)
    assert evidence["tls_source_target_proof_sha256"] == value.source_target_proof.sha256
    assert "unit-secret" not in str(evidence) and "unit-only-client.key" not in str(evidence)


@pytest.mark.parametrize(
    "change",
    [
        {"host": "production"},
        {"port": 5432},
        {"query": {"sslmode": "disable"}},
        {"query": {"hostaddr": "source"}},
        {"username": "other"},
        {"database": "another_restore_test"},
        {"drivername": "postgresql"},
    ],
)
def test_tls_target_rejects_endpoints_or_url_overrides_before_authority(binding, change):
    value, calls = binding
    with pytest.raises(ArchiveBlocked, match="loopback19442"):
        recovery.restore_tls_connect_args(value, make_url(URL).set(**change))
    assert not calls


@pytest.mark.parametrize(
    "mutation",
    ["same_bytes_new_inode", "same_path_new_bytes", "writable_key", "hardlink", "symlink"],
)
def test_root_artifact_generation_permissions_and_sha_are_enforced(binding, mutation):
    value, calls = binding
    pin = value.client_key
    if mutation == "same_bytes_new_inode":
        old = pin.path.read_bytes()
        pin.path.unlink()
        pin.path.write_bytes(old)
        pin.path.chmod(0o400)
    elif mutation == "same_path_new_bytes":
        pin.path.chmod(0o600)
        pin.path.write_bytes(b"changed")
        pin.path.chmod(0o400)
    elif mutation == "writable_key":
        pin.path.chmod(0o600)
    elif mutation == "hardlink":
        os.link(pin.path, pin.path.with_name("linked.key"))
    else:
        pin.path.unlink()
        pin.path.symlink_to(value.client_certificate.path)
    with pytest.raises(ArchiveBlocked, match="TLS file|TLS artifact"):
        recovery.restore_tls_connect_args(value, make_url(URL))
    assert not calls


def test_wrong_pinned_sha_and_duplicate_artifacts_block(binding):
    value, calls = binding
    with pytest.raises(ArchiveBlocked, match="SHA"):
        recovery.restore_tls_connect_args(
            replace(value, client_key=replace(value.client_key, sha256="f" * 64)), make_url(URL)
        )
    with pytest.raises(ArchiveBlocked, match="Distinct"):
        recovery.restore_tls_connect_args(
            replace(value, client_key=value.client_certificate), make_url(URL)
        )
    assert not calls


def test_fresh_source_guard_failure_precedes_any_database_or_client(binding, tmp_path, monkeypatch):
    value, calls = binding

    def expired(current, artifact):
        assert artifact == b"unit-only-source-proof"
        raise ArchiveBlocked("Unit Source generation expired")

    value = replace(value, guard=expired)

    def forbidden(*args, **kwargs):
        pytest.fail("Expired Source authority must precede PostgreSQL")

    monkeypatch.setattr(database_restore, "run_client", forbidden)
    monkeypatch.setattr(database_restore, "create_engine", forbidden)
    for invoke in (
        lambda: database_restore.restore_backup(
            tmp_path, URL, "0" * 64, b"", checkpoint=lambda: None, tls_binding=value
        ),
        lambda: recovery.restore_database(
            tmp_path, URL, "0" * 64, checkpoint=lambda: None, tls_binding=value
        ),
    ):
        with pytest.raises(ArchiveBlocked, match="Source generation expired"):
            invoke()
    assert not calls


def test_checkpoint_rechecks_actual_file_generation_and_root_authority(binding):
    value, calls = binding
    base = []
    checkpoint = recovery.restore_tls_checkpoint(value, make_url(URL), lambda: base.append(True))
    checkpoint()
    assert len(calls) == 2 and base == [True]
    value.source_target_proof.path.chmod(0o600)
    with pytest.raises(ArchiveBlocked, match="Immutable"):
        checkpoint()
    assert len(calls) == 2


def test_cli_guard_pins_identity_marker_generation_and_tls_before_scripts(binding):
    value, _ = binding
    guard = recovery.restore_tls_guard_sql(value, make_url(URL))
    for needle in (
        value.target.database,
        str(value.target.database_oid),
        str(value.target.role_oid),
        value.target.postmaster_start,
        value.target.marker_sha256,
        "inet_server_port() IS DISTINCT FROM 19442",
        "150010",
        "pg_stat_ssl",
        "ssl AND bits>=128",
        "session_user",
        "rolsuper",
        "TEMPORARY",
    ):
        assert needle in guard
    assert guard.startswith("SET LOCAL search_path=pg_catalog;\nDO $greenmind_tls_target$")
    assert "unit-secret" not in guard and "client.key" not in guard


def test_before_connect_rejects_changed_tls_or_endpoint_parameters(binding, monkeypatch):
    from sqlalchemy import event

    value, calls = binding
    handlers = []
    monkeypatch.setattr(
        event,
        "listen",
        lambda engine, name, function: handlers.append(function)
        if name == "do_connect"
        else pytest.fail("Unexpected event"),
    )
    recovery.restore_tls_engine_guard(object(), value, make_url(URL), checkpoint=lambda: None)
    parameters = dict(
        recovery.restore_tls_connect_args(value, make_url(URL)),
        host="127.0.0.1",
        port=19442,
        dbname=value.target.database,
        user=value.target.role,
        password="unit-secret",
    )
    handlers[0](None, None, [], parameters)
    for change in (
        {"sslmode": "disable"},
        {"hostaddr": "production"},
        {"port": 5432},
        {"sslkey": "/elsewhere/key"},
    ):
        with pytest.raises(ArchiveBlocked, match="driver TLS"):
            handlers[0](None, None, [], parameters | change)
    assert calls


def test_live_sql_reuses_existing_seven_pin_parent_guard_then_requires_tls(binding, monkeypatch):
    value, _ = binding
    guarded = []

    def target(db, engine, target, marker, source, checkpoint, target_guard):
        guarded.append((target, marker, source))
        checkpoint()
        target_guard()

    monkeypatch.setattr(parent_companion, "_target", target)
    state = {"encrypted": True}

    class DB:
        def execute(self, statement):
            assert "pg_stat_ssl" in str(statement)
            return SimpleNamespace(scalar_one=lambda: state["encrypted"])

    engine = SimpleNamespace(url=make_url(URL))
    recovery.restore_tls_identity(DB(), engine, value, checkpoint=lambda: None)
    assert guarded == [(value.target.identity(), value.target.marker_sha256, "gateway")]
    state["encrypted"] = False
    with pytest.raises(ArchiveBlocked, match="actual PostgreSQL TLS"):
        recovery.restore_tls_identity(DB(), engine, value, checkpoint=lambda: None)


def test_legacy_backup_clients_and_engine_receive_same_pinned_tls(binding, tmp_path, monkeypatch):
    import json
    from contextlib import nullcontext

    from app.raw_archive.policy import checksum
    from app.tiered_archive import parquet
    from tests.test_tiered_database_restore import package

    value, authority_calls = binding
    folder, sha, key = package(tmp_path, proof_version=3)
    manifest = json.loads((folder / "manifest.json").read_text())
    proof = list(parquet.read(folder / "0000.parquet", manifest["files"][0]))[0]["body"][
        "master_proof"
    ]
    calls, identities, engines = [], [], []

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execution_options(self, **options):
            return self

        def begin(self):
            return nullcontext()

        def execute(self, statement):
            assert str(statement).startswith(
                ("SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout")
            )

    engine = SimpleNamespace(url=make_url(URL), connect=DB, dispose=lambda: None)

    def create(url, **options):
        assert url == make_url(URL)
        engines.append(options)
        return engine

    def client(command, environment, *, checkpoint, output=None):
        checkpoint()
        calls.append((command, environment))
        assert environment["PGSSLMODE"] == "verify-full"
        assert environment["PGHOSTADDR"] == environment["PGHOST"] == "127.0.0.1"
        assert environment["PGPORT"] == "19442"
        for name, pin in [
            ("PGSSLROOTCERT", value.root_certificate),
            ("PGSSLCERT", value.client_certificate),
            ("PGSSLKEY", value.client_key),
        ]:
            assert environment[name] == str(pin.path)
        assert "PGSERVICE" not in environment
        if "--list" in command:
            output.write_bytes(b"1; 1259 42 TABLE public fixture owner\n")
        elif output is not None:
            output.write_bytes(b"-- synthetic unit SQL only\n")
        else:
            assert command[0].endswith("psql")
            guard = next(Path(arg) for arg in command if arg.endswith("/guard.sql")).read_text()
            assert guard.index("Pinned TLS restore target changed") < guard.index(
                "Restore requires an unprivileged"
            )

    monkeypatch.setattr(database_restore, "run_client", client)
    monkeypatch.setattr(database_restore, "create_engine", create)
    monkeypatch.setattr(database_restore, "restore_tls_engine_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        database_restore,
        "restore_tls_identity",
        lambda db, engine, current, **kwargs: identities.append(current),
    )
    monkeypatch.setattr(database_restore, "master_proof", lambda *args, **kwargs: proof)
    result = database_restore.restore_backup(
        folder, URL, sha, key, checkpoint=lambda: None, tls_binding=value
    )
    assert len(calls) == 5 and identities == [value]
    assert engines[0]["connect_args"] == {
        "connect_timeout": 3,
        **recovery.restore_tls_connect_args(value, make_url(URL)),
    }
    assert (
        result["typed_values_verified"] is True
        and result["tls_source_target_proof_sha256"] == value.source_target_proof.sha256
    )
    assert checksum(folder / "manifest.json") == sha and len(authority_calls) >= len(calls)


def test_source_guard_cannot_replace_root_artifact_during_observation(binding):
    value, _ = binding

    def changed(current, artifact):
        current.client_key.path.chmod(0o600)

    with pytest.raises(ArchiveBlocked, match="Immutable"):
        recovery.restore_tls_connect_args(replace(value, guard=changed), make_url(URL))


def test_target_timestamp_cannot_close_cli_do_body(binding):
    value, _ = binding
    with pytest.raises(ArchiveBlocked, match="loopback19442"):
        recovery.restore_tls_guard_sql(
            replace(
                value, target=replace(value.target, postmaster_start="$greenmind_tls_target$;bad")
            ),
            make_url(URL),
        )


def test_provider_clients_and_readback_engine_propagate_identical_tls(
    binding, tmp_path, monkeypatch
):
    from tests.test_tiered_schema_provider import new_bundle, toc_text

    value, _ = binding
    target = replace(
        value.target, database=value.target.database.removesuffix("gateway") + "direct"
    )
    value = replace(value, target=target, guard=lambda current, artifact: None)
    url = make_url(URL).set(database=target.database)
    original, folder, sha, key, proof, schema_toc, masters_toc = new_bundle(tmp_path, monkeypatch)
    calls = []

    class EngineReached(Exception):
        pass

    def client(command, environment, *, checkpoint, output=None):
        checkpoint()
        calls.append(command)
        assert environment["PGDATABASE"] == target.database and environment["PGPORT"] == "19442"
        assert (
            environment["PGSSLMODE"] == "verify-full" and environment["PGHOSTADDR"] == "127.0.0.1"
        )
        assert environment["PGSSLROOTCERT"] == str(value.root_certificate.path)
        assert environment["PGSSLCERT"] == str(value.client_certificate.path)
        assert environment["PGSSLKEY"] == str(value.client_key.path)
        assert not {"PGSERVICE", "PGSERVICEFILE"} & environment.keys()
        if "--list" in command:
            if command[-1].endswith("schema.dump"):
                output.write_bytes(
                    toc_text(
                        proof["provider"]["scope"]["database"],
                        ["1; 3079 100 EXTENSION - timescaledb -"],
                    )
                )
            else:
                output.write_bytes(
                    schema_toc if command[-1].endswith("provider.dump") else masters_toc
                )
        elif output is not None:
            output.write_bytes(b"-- synthetic unit script\n")
        else:
            assert "--single-transaction" in command
            guard = next(Path(arg) for arg in command if arg.endswith("/guard.sql")).read_text()
            assert guard.index("Pinned TLS restore target changed") < guard.index(
                "Exact empty provider target required"
            )

    def create(actual_url, **options):
        assert actual_url == url
        assert options["connect_args"] == {
            "connect_timeout": 3,
            "options": "-c default_transaction_read_only=off -c statement_timeout=8000 -c lock_timeout=150 -c idle_in_transaction_session_timeout=30000",
            **recovery.restore_tls_connect_args(value, url),
        }
        raise EngineReached

    monkeypatch.setattr(database_restore, "run_client", client)
    monkeypatch.setattr(database_restore, "create_engine", create)
    authority = {
        "coordinator": "existing-native-timescale-restore-v1",
        "database": target.database,
        "oid": target.database_oid,
        "owner_oid": target.role_oid,
        "marker_sha256": target.marker_sha256,
    }
    try:
        with pytest.raises(EngineReached):
            database_restore.restore_backup(
                folder,
                url,
                sha,
                key,
                checkpoint=lambda: None,
                provider_target=authority,
                post_restore=lambda **kwargs: None,
                tls_binding=value,
            )
        assert len(calls) == 8
    finally:
        original.dispose()


def test_tls_target_source_cannot_be_silently_reused(binding):
    value, _ = binding
    recovery.restore_tls_source(value, "gateway")
    with pytest.raises(ArchiveBlocked, match="another restore source"):
        recovery.restore_tls_source(value, "direct")


@pytest.mark.parametrize("changed", ["owner", "group", "parent_access"])
def test_artifacts_reject_wrong_root_identity_or_exposed_parent(binding, monkeypatch, changed):
    value, calls = binding
    original = Path.lstat
    target = value.client_key.path if changed != "parent_access" else value.client_key.path.parent

    def altered(path):
        info = original(path)
        if path != target:
            return info
        overrides = {
            "owner": {"st_uid": 501},
            "group": {"st_gid": 501},
            "parent_access": {"st_mode": 0o40750},
        }[changed]
        return SimpleNamespace(
            **{
                name: overrides.get(name, getattr(info, name))
                for name in (
                    "st_dev",
                    "st_ino",
                    "st_size",
                    "st_uid",
                    "st_gid",
                    "st_mode",
                    "st_nlink",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
            }
        )

    monkeypatch.setattr(Path, "lstat", altered)
    with pytest.raises(ArchiveBlocked, match="Root TLS"):
        recovery.restore_tls_connect_args(value, make_url(URL))
    assert not calls
