"""Synthetic preparation only: no server, database, account or transport calls."""

import copy
import importlib.util
import json
import shlex
import stat
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "production-copy-profile.py"
SPEC = importlib.util.spec_from_file_location("production_copy_profile", PATH)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
SECRET = "synthetic-password-never-print"


def request():
    original = b'PYTHONPATH="/opt/greenmind/repair-thischat-20261012/backend"\n'
    old_url = (
        "postgresql://metadata:"
        + SECRET
        + "@127.0.0.1:5432/greenmind_direct_production"
    )
    reader = (
        "postgresql+psycopg2://reader:"
        + SECRET
        + "@127.0.0.1:6432/greenmind_direct_production"
    )
    identity = {
        "database": "greenmind_direct_production",
        "server_addr": "172.28.20.5/32",
        "server_port": 5432,
    }
    env = dict.fromkeys(m.FALSE, "false")
    env.update(
        RAW_ARCHIVE_ENABLED="true",
        RAW_ARCHIVE_ENVIRONMENT="production",
        RAW_ARCHIVE_WORKER_ROLE="archive",
        RAW_ARCHIVE_STATE_DIR=m.STATE,
        RAW_ARCHIVE_MAX_SECONDS="75",
        RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB="800",
        RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB="640",
        RAW_ARCHIVE_SOURCE_MAX_LOAD="2",
        RAW_ARCHIVE_SFTP_ROOT="greenmind-raw",
        RAW_ARCHIVE_SFTP_PORT="23",
        RAW_ARCHIVE_SFTP_HOST="existing-box.example",
        RAW_ARCHIVE_SFTP_USER="existing-account",
        RAW_ARCHIVE_SFTP_KEY="/etc/existing/upload-key",
        RAW_ARCHIVE_SFTP_KNOWN_HOSTS="/etc/existing/known-hosts",
        RAW_ARCHIVE_COORDINATION_DIR="/var/lib/greenmind/archive-coordination",
        RAW_ARCHIVE_DIRECT_DATABASE_URL=old_url,
    )
    files = [
        {"path": "/etc/greenmind/archive-copy-20261010/copy.env", "sha256": "1" * 64}
    ]
    files += copy.deepcopy(files)
    files += [{"path": m.OVERLAY, "sha256": m.sha(original)}]
    return dict(
        copy_environment=env,
        original_overlay=original,
        overlay_path=m.OVERLAY,
        unit=dict(
            id=m.SERVICE,
            user="greenmind-archive-copy",
            group="greenmind-archive-copy",
            memory_max_bytes=192 * 1024**2,
            memory_high_bytes=128 * 1024**2,
            cpu_percent=25,
            read_write_paths=[m.STATE, "/var/lib/greenmind/archive-coordination"],
            admission_sha256=m.ADMISSION_SHA256,
            entry_argv_sha256=m.COPY_ENTRY_SHA256,
            unit_files={"/etc/systemd/system/greenmind-archive-copy.service": "4" * 64},
            environment_files=files,
            timer=dict(
                id=m.TIMER, active=True, enabled=True, definition_sha256="5" * 64
            ),
        ),
        owner=dict(
            schema=1,
            accepted=True,
            owner="archive",
            environment="production",
            cohort="a" * 24,
            source_journal_sha256="6" * 64,
            file_sha256="7" * 64,
        ),
        release=dict(
            commit="b" * 40,
            runtime_sha256="8" * 64,
            manifest_sha256="9" * 64,
            backend_path="/opt/greenmind/tiered-archive/releases/"
            + "b" * 40
            + "/backend",
            capabilities=[m.CONTRACT],
        ),
        direct_reader=dict(
            url=reader,
            proof=dict(
                url_sha256=m.sha(reader.encode()),
                transaction_read_only=True,
                select_projection_error=True,
                source_writes=False,
                copy_database_identity=identity,
                reader_database_identity=copy.deepcopy(identity),
            ),
        ),
        pilot=dict(
            wav_jobs=[
                dict(
                    source="gateway", reference=["11111111-1111-4111-8111-111111111111"]
                ),
                dict(
                    source="direct",
                    reference=["22222222-2222-4222-8222-222222222222", 7],
                ),
            ],
            wav_limits=dict(max_wavs=10, max_bytes=5242880),
        ),
    )


def values(raw):
    return {
        k: shlex.split(v)[0]
        for line in raw.decode().splitlines()
        for k, v in [line.split("=", 1)]
    }


def test_exact_scope_preserves_base_transport_owner_limits_and_original_bytes():
    req = request()
    before = copy.deepcopy(req)
    bundle = m.prepare(**req)
    assert req == before
    assert bundle["original-profile.env"] == req["original_overlay"]
    schedule = json.loads(bundle["schedule.json"])
    assert set(schedule) == {"schema", "environment", "wav_jobs", "wav_limits"}
    assert schedule["wav_jobs"] == req["pilot"]["wav_jobs"]
    profile = values(bundle["profile.env"])
    assert profile["RAW_ARCHIVE_COPY_MODE"] == "tiered-copy-only"
    assert profile["RAW_ARCHIVE_DIRECT_DATABASE_URL"] == req["direct_reader"]["url"]
    assert all(profile[x] == "false" for x in m.FALSE)
    assert profile["TIERED_ARCHIVE_STATE_DIR"].startswith(
        m.STATE + "/tiered-copy-pilot-"
    )
    assert "RAW_ARCHIVE_GATEWAY_DATABASE_URL" not in profile
    assert "RAW_ARCHIVE_SFTP_KEY" not in profile  # inherited binding, never recopied
    plan = json.loads(bundle["install-plan.private.json"])
    assert plan["existing_unit_binding"] == req["unit"]
    assert plan["existing_owner_binding"] == req["owner"]
    assert plan["legacy_state_retained"] == m.STATE
    assert (
        plan["snapshot_jobs"]
        == plan["database_backup_jobs"]
        == plan["sql_jobs"]
        == plan["delete_jobs"]
        == 0
    )
    assert SECRET not in bundle["install-plan.private.json"].decode()


@pytest.mark.parametrize(
    "extra", ["scopes", "backups", "wav_sources", "snapshot_id", "scheduled_snapshots"]
)
def test_scans_other_jobs_and_snapshot_steps_never_enter_copy_pilot(extra):
    req = request()
    req["pilot"][extra] = []
    with pytest.raises(ValueError, match="explicit_jobs_only"):
        m.prepare(**req)


@pytest.mark.parametrize(
    "limits",
    [
        dict(max_wavs=11, max_bytes=1),
        dict(max_wavs=1, max_bytes=5242881),
        dict(max_wavs=True, max_bytes=1),
        dict(max_wavs=1, max_bytes=0),
    ],
)
def test_first_reached_global_budget_limits_are_closed(limits):
    req = request()
    req["pilot"]["wav_limits"] = limits
    with pytest.raises(ValueError, match="pilot_budget"):
        m.prepare(**req)


@pytest.mark.parametrize(
    "change", ["duplicate", "bool_revision", "missing_revision", "uncanonical_uuid"]
)
def test_exact_reference_scope(change):
    req = request()
    jobs = req["pilot"]["wav_jobs"]
    if change == "duplicate":
        jobs.append(copy.deepcopy(jobs[0]))
    elif change == "bool_revision":
        jobs[1]["reference"][1] = True
    elif change == "missing_revision":
        jobs[1]["reference"].pop()
    else:
        jobs[0]["reference"][0] = "11111111111141118111111111111111"
    with pytest.raises(ValueError):
        m.prepare(**req)


@pytest.mark.parametrize(
    "change",
    [
        "legacy_runtime",
        "enabled_delete",
        "long_runtime",
        "wrong_owner",
        "different_original",
        "new_service",
        "more_memory",
        "wrong_target",
        "changed_admission",
        "changed_entry",
    ],
)
def test_safety_and_existing_identity_pins_cannot_be_broadened(change):
    req = request()
    if change == "legacy_runtime":
        req["release"]["capabilities"] = []
    elif change == "enabled_delete":
        req["copy_environment"]["RAW_ARCHIVE_DELETE_ENABLED"] = "true"
    elif change == "long_runtime":
        req["copy_environment"]["RAW_ARCHIVE_MAX_SECONDS"] = "76"
    elif change == "wrong_owner":
        req["owner"]["accepted"] = False
    elif change == "different_original":
        req["original_overlay"] += b"#changed\n"
    elif change == "new_service":
        req["unit"]["id"] = "another-copy.service"
    elif change == "more_memory":
        req["unit"]["memory_max_bytes"] += 1
    elif change == "changed_admission":
        req["unit"]["admission_sha256"] = "0" * 64
    elif change == "changed_entry":
        req["unit"]["entry_argv_sha256"] = "0" * 64
    else:
        req["overlay_path"] = "/etc/passwd"
    with pytest.raises(ValueError):
        m.prepare(**req)


@pytest.mark.parametrize(
    "change", ["missing_error", "writable", "different_database", "changed_password"]
)
def test_reused_direct_reader_needs_bound_readonly_same_database_error_right(change):
    req = request()
    proof = req["direct_reader"]["proof"]
    if change == "missing_error":
        proof["select_projection_error"] = False
    elif change == "writable":
        proof["source_writes"] = True
    elif change == "different_database":
        proof["reader_database_identity"]["server_addr"] = "different"
    else:
        req["direct_reader"]["url"] = req["direct_reader"]["url"].replace(
            SECRET, "changed"
        )
    with pytest.raises(ValueError, match="proof_required"):
        m.prepare(**req)


def test_private_artifacts_and_public_summary_do_not_leak_server_credentials(tmp_path):
    bundle = m.prepare(**request())
    folder = tmp_path / "private"
    result = m.write_private_bundle(folder, bundle)
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in folder.iterdir())
    assert SECRET not in json.dumps(result)
    assert result["installed"] is False
    with pytest.raises(FileExistsError):
        m.write_private_bundle(folder, bundle)


def test_systemd_environment_data_quoting_is_literal():
    value = 'dollar$ backtick` quote" slash\\ hash#'
    assert values(m.render_env({"VALUE": value}))["VALUE"] == value
    with pytest.raises(ValueError):
        m.render_env({"VALUE": "line\nsecret"})


def test_direct_revision_matches_postgresql_integer_bound():
    req = request()
    req["pilot"]["wav_jobs"][1]["reference"][1] = 2**31
    with pytest.raises(ValueError):
        m.prepare(**req)


def test_local_cli_rejects_public_request_without_credential_echo(tmp_path):
    import subprocess
    import sys

    req = request()
    req["original_overlay"] = req["original_overlay"].decode()
    file = tmp_path / "public-request.json"
    file.write_text(json.dumps(req))
    file.chmod(0o644)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(PATH),
            "--request",
            str(file),
            "--output",
            str(tmp_path / "output"),
        ],
        capture_output=True,
    )
    assert result.returncode == 2
    assert SECRET.encode() not in result.stdout + result.stderr
    assert not (tmp_path / "output").exists()


def test_emitted_plan_matches_actual_integrated_runtime_contract():
    import ast
    import uuid

    source = PATH.parents[2] / "backend/app/tiered_archive/schedule.py"
    tree = ast.parse(source.read_text())
    nodes = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "exact_wav_jobs"
    ]
    if not nodes:
        pytest.skip(
            "Exact runtime contract must be integrated before pilot installation"
        )
    scope = {"uuid": uuid, "ArchiveBlocked": ValueError}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)
    plan = json.loads(m.prepare(**request())["schedule.json"])
    jobs, limits = scope["exact_wav_jobs"](plan)
    assert jobs == plan["wav_jobs"] and limits == plan["wav_limits"]
