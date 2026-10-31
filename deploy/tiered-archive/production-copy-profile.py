"""Pure, private production pilot preparation; never installs or starts anything."""

import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import uuid
from pathlib import Path
from urllib.parse import urlsplit

CONTRACT = "production-explicit-wav-jobs-v1"
SERVICE = "greenmind-archive-copy.service"
TIMER = "greenmind-archive-copy.timer"
STATE = "/var/lib/greenmind/archive-copy-20261010"
OVERLAY = "/opt/greenmind/repair-thischat-20261012/runtime.env"
ADMISSION_SHA256 = "4d250e2505ae1a0c709193de94f3ae5e8033e6510f75f20fe90215d6c888cc5e"
COPY_ENTRY_SHA256 = "ce7623acb3d996d675800beaca9cc42d44a8c1a40a9bb9faaa737db463354106"
MAX_BYTES = 5 * 1024**2
FALSE = (
    "RAW_ARCHIVE_DELETE_ENABLED",
    "RAW_ARCHIVE_LEGACY_NULL_ACCEPTED",
    "RETENTION_ENABLED",
    "DIRECT_RETENTION_ENABLED",
    "VISUAL_PRUNE_ENABLED",
)


def need(value, reason):
    if not value:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"


def pin(value):
    return isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value) is not None


def path(value):
    need(
        isinstance(value, str)
        and re.fullmatch(r"/[a-zA-Z0-9_./-]+", value)
        and ".." not in Path(value).parts,
        "absolute_binding_path_required",
    )
    return value


def env_values(raw):
    out = {}
    for line in raw.decode().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        need(separator and re.fullmatch("[A-Z][A-Z0-9_]*", key), "overlay_syntax")
        parts = shlex.split(value, comments=True)
        need(len(parts) <= 1 and key not in out, "overlay_syntax")
        out[key] = parts[0] if parts else ""
    return out


def render_env(values):
    lines = []
    for key, value in sorted(values.items()):
        need(
            re.fullmatch("[A-Z][A-Z0-9_]*", key)
            and isinstance(value, str)
            and not any(c in value for c in "\r\n\0"),
            "single_line_environment_required",
        )
        # systemd EnvironmentFile is data, not a shell script; no expansion.
        quoted = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(key + '="' + quoted + '"')
    return ("\n".join(lines) + "\n").encode()


def exact_schedule(pilot):
    need(set(pilot) == {"wav_jobs", "wav_limits"}, "explicit_jobs_only")
    limits = pilot["wav_limits"]
    need(
        set(limits) == {"max_wavs", "max_bytes"}
        and type(limits["max_wavs"]) is int
        and 1 <= limits["max_wavs"] <= 10
        and type(limits["max_bytes"]) is int
        and 1 <= limits["max_bytes"] <= MAX_BYTES,
        "pilot_budget_exceeded",
    )
    jobs = pilot["wav_jobs"]
    need(
        isinstance(jobs, list) and 1 <= len(jobs) <= 10, "small_exact_job_list_required"
    )
    seen = set()
    for item in jobs:
        need(
            isinstance(item, dict) and set(item) == {"source", "reference"},
            "exact_job_shape",
        )
        source, reference = item["source"], item["reference"]
        need(
            source in {"gateway", "direct"}
            and isinstance(reference, list)
            and len(reference) == (1 if source == "gateway" else 2),
            "exact_job_reference",
        )
        try:
            need(
                str(uuid.UUID(reference[0])) == reference[0], "canonical_uuid_required"
            )
        except (ValueError, TypeError, AttributeError):
            raise ValueError("canonical_uuid_required") from None
        need(
            source != "direct"
            or type(reference[1]) is int
            and 1 <= reference[1] <= 2**31 - 1,
            "exact_direct_revision_required",
        )
        token = (source, *reference)
        need(token not in seen, "duplicate_pilot_job")
        seen.add(token)
    return {"schema": 1, "environment": "production", **pilot}


def prepare(
    *,
    copy_environment,
    original_overlay,
    overlay_path,
    unit,
    owner,
    release,
    direct_reader,
    pilot,
):
    """Inputs/credentials remain on the operator host; output files stay private."""
    env = copy_environment
    need(
        all(env.get(key) == "false" for key in FALSE)
        and env.get("RAW_ARCHIVE_ENABLED") == "true"
        and env.get("RAW_ARCHIVE_ENVIRONMENT") == "production"
        and env.get("RAW_ARCHIVE_WORKER_ROLE") == "archive"
        and env.get("RAW_ARCHIVE_STATE_DIR") == STATE,
        "existing_copy_guard_must_be_preserved",
    )
    need(
        1 <= int(env["RAW_ARCHIVE_MAX_SECONDS"]) <= 75
        and int(env["RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB"]) >= 800
        and int(env["RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB"]) >= 640
        and float(env["RAW_ARCHIVE_SOURCE_MAX_LOAD"]) <= 2.4,
        "existing_resource_guard_required",
    )
    need(
        env.get("RAW_ARCHIVE_SFTP_ROOT") == "greenmind-raw"
        and env.get("RAW_ARCHIVE_SFTP_PORT", "23") == "23",
        "existing_box_root_required",
    )
    for key in (
        "RAW_ARCHIVE_SFTP_HOST",
        "RAW_ARCHIVE_SFTP_USER",
        "RAW_ARCHIVE_SFTP_KEY",
        "RAW_ARCHIVE_SFTP_KNOWN_HOSTS",
        "RAW_ARCHIVE_COORDINATION_DIR",
    ):
        need(bool(env.get(key)), "existing_transport_binding_required")
    need(
        unit["id"] == SERVICE
        and unit["user"] == "greenmind-archive-copy"
        and unit["group"] == "greenmind-archive-copy"
        and unit["memory_max_bytes"] == 192 * 1024**2
        and unit["cpu_percent"] == 25
        and STATE in unit["read_write_paths"]
        and unit["admission_sha256"] == ADMISSION_SHA256
        and unit["entry_argv_sha256"] == COPY_ENTRY_SHA256,
        "existing_single_service_admission_required",
    )
    need(
        unit["timer"]["id"] == TIMER
        and type(unit["timer"]["active"]) is bool
        and type(unit["timer"]["enabled"]) is bool
        and pin(unit["timer"]["definition_sha256"]),
        "existing_timer_identity_required",
    )
    need(
        unit["unit_files"] and all(pin(v) for v in unit["unit_files"].values()),
        "unit_file_pins_required",
    )
    need(
        owner.get("schema") == 1
        and owner.get("accepted") is True
        and owner.get("owner") == "archive"
        and owner.get("environment") == "production"
        and re.fullmatch("[a-f0-9]{24}", owner.get("cohort", ""))
        and pin(owner.get("source_journal_sha256"))
        and pin(owner.get("file_sha256")),
        "existing_accepted_single_writer_owner_required",
    )
    need(
        CONTRACT in release["capabilities"]
        and pin(release["runtime_sha256"])
        and pin(release["manifest_sha256"])
        and re.fullmatch("[a-f0-9]{40}", release["commit"]),
        "explicit_wav_job_runtime_not_available",
    )
    backend = path(release["backend_path"])
    need(
        backend.startswith("/opt/greenmind/") and Path(backend).name == "backend",
        "immutable_release_backend_required",
    )
    original = env_values(original_overlay)
    overlay_path = path(overlay_path)
    need(
        overlay_path == OVERLAY and set(original) == {"PYTHONPATH"},
        "known_code_overlay_only",
    )
    need(
        unit["environment_files"][-1]["path"] == overlay_path
        and unit["environment_files"][-1]["sha256"] == sha(original_overlay)
        and all(pin(x["sha256"]) for x in unit["environment_files"]),
        "exact_original_overlay_required",
    )
    reader_url = direct_reader["url"]
    need(
        isinstance(reader_url, str) and not any(c in reader_url for c in "\r\n\0"),
        "private_reader_binding_required",
    )
    try:
        parsed = urlsplit(reader_url)
        reader_port = parsed.port
        old = urlsplit(env["RAW_ARCHIVE_DIRECT_DATABASE_URL"])
    except (ValueError, TypeError):
        raise ValueError("existing_direct_reader_binding_required") from None
    need(
        parsed.scheme in {"postgresql", "postgresql+psycopg2"}
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and reader_port == 6432
        and parsed.path == old.path == "/greenmind_direct_production"
        and not parsed.fragment,
        "existing_direct_reader_binding_required",
    )
    proof = direct_reader["proof"]
    need(
        proof.get("url_sha256") == sha(reader_url.encode())
        and proof.get("transaction_read_only") is True
        and proof.get("select_projection_error") is True
        and proof.get("source_writes") is False
        and proof.get("copy_database_identity") == proof.get("reader_database_identity")
        and isinstance(proof.get("copy_database_identity"), dict)
        and set(proof["copy_database_identity"])
        == {"database", "server_addr", "server_port"}
        and proof["copy_database_identity"]["database"]
        == "greenmind_direct_production",
        "direct_readiness_role_proof_required",
    )
    schedule = canonical(exact_schedule(pilot))
    scope = sha(schedule)[:16]
    root = STATE + "/tiered-copy-pilot-" + scope
    backup_root = "/etc/greenmind/tiered-archive/production-copy-pilot-" + scope
    overlay = dict(
        original,
        PYTHONPATH=backend,
        RAW_ARCHIVE_COPY_MODE="tiered-copy-only",
        RAW_ARCHIVE_DIRECT_DATABASE_URL=reader_url,
        TIERED_ARCHIVE_ENABLED="true",
        TIERED_ARCHIVE_ENVIRONMENT="production",
        TIERED_ARCHIVE_DELETE_ENABLED="false",
        TIERED_ARCHIVE_READS_ENABLED="false",
        TIERED_ARCHIVE_STATE_DIR=root,
        TIERED_ARCHIVE_CACHE_DIR=root + "/cache",
        TIERED_ARCHIVE_SCHEDULE_FILE=root + "/schedule.json",
    )
    # Explicitly retain every deletion guard even if an earlier profile changes.
    overlay.update({key: "false" for key in FALSE})
    profile = render_env(overlay)
    manifest = {
        "schema": "greenmind.production-copy-pilot-preparation.v1",
        "prepared_only": True,
        "installed": False,
        "service": SERVICE,
        "timer": TIMER,
        "scope": scope,
        "existing_unit_binding": unit,
        "existing_owner_binding": owner,
        "existing_owner_path": "/etc/greenmind/archive-copy-owner.json",
        "release_binding": release,
        "legacy_state_retained": STATE,
        "state_root": root,
        "private_backup_root": backup_root,
        "files": {
            "profile.env": {
                "target": overlay_path,
                "sha256": sha(profile),
                "owner": "root",
                "mode": "0600",
            },
            "schedule.json": {
                "target": root + "/schedule.json",
                "sha256": sha(schedule),
                "owner": unit["user"],
                "group": unit["group"],
                "mode": "0600",
            },
            "original-profile.env": {
                "target": backup_root + "/original-runtime.env",
                "sha256": sha(original_overlay),
                "owner": "root",
                "mode": "0600",
            },
        },
        "activation_preconditions": [
            "fresh root-owned release, unit, admission, owner and environment-file hashes match",
            "existing fixed archive-python-20261008-v4 interpreter imports pinned new dependencies",
            "existing timer quiesced, enabled state unchanged; existing service inactive",
            "fresh source health, original resource reserves and exact job metadata pass",
            "all new state/cache path ancestors are symlink-free; no original state replaced",
        ],
        "ordered_install": [
            "preserve original timer enabled/active states; temporarily quiesce same timer",
            "wait for existing copy service to finish; no forced termination",
            "create private backup and new scoped state/cache directories, retain all originals",
            "write schedule and exact original-profile backup before touching runtime overlay",
            "compare original bytes and every pinned dependency again; atomically replace only overlay",
            "manually start the same existing copy service once; observe actual terminal and receipts",
        ],
        "ordered_rollback": [
            "keep same timer quiesced until the bounded service actually ends",
            "restore original overlay bytes only if candidate SHA still matches; otherwise stop for review",
            "restore original timer active state and retain its original enabled state",
            "keep new pilot evidence, ledger and Box objects; delete nothing",
        ],
        "maintenance": {
            "same_existing_timer_only": True,
            "enabled_state_mutations": 0,
            "quiesce_before_profile_switch": True,
            "restore_original_overlay_after_pilot": True,
            "original_timer_active": unit["timer"]["active"],
            "original_timer_enabled": unit["timer"]["enabled"],
            "daemon_reload_required": False,
            "force_kill_allowed": False,
        },
        "directories": [
            {"path": backup_root, "owner": "root", "mode": "0700"},
            {
                "path": root,
                "owner": unit["user"],
                "group": unit["group"],
                "mode": "0700",
            },
            {
                "path": root + "/cache",
                "owner": unit["user"],
                "group": unit["group"],
                "mode": "0700",
            },
        ],
        "profile_compare_and_swap": {
            "target": overlay_path,
            "expected_old_sha256": sha(original_overlay),
            "candidate_sha256": sha(profile),
            "backup_artifact": "original-profile.env",
            "rollback_requires_current_candidate_sha256": True,
        },
        "pilot_success_requires": [
            "real existing-service terminal observed; no source/receiver or resource failure",
            "every exact reference has a published complete bundle and full readback receipt",
            "no blocked/deferred scope remains; no source deletion or dataset scan occurred",
            "original unit, owner, base environments and legacy read paths remain unchanged",
        ],
        "fixed_copy_interpreter": "/opt/greenmind/archive-python-20261008-v4/bin/python",
        "runtime_dependency_compatibility_proven_by_preparation": False,
        "direct_role_rebinding_requires_new_tiered_binding": True,
        "new_services": 0,
        "new_timers": 0,
        "source_write_jobs": 0,
        "sql_jobs": 0,
        "database_backup_jobs": 0,
        "snapshot_jobs": 0,
        "delete_jobs": 0,
        "snapshot_restore_proof": "separate existing-authorized-bindings-only follow-up; not claimed",
    }
    return {
        "profile.env": profile,
        "schedule.json": schedule,
        "original-profile.env": original_overlay,
        "install-plan.private.json": canonical(manifest),
    }


def write_private_bundle(folder, artifacts):
    """Writes preparation files only. No target config paths are opened."""
    folder = Path(folder)
    folder.mkdir(mode=0o700)
    for name, raw in artifacts.items():
        need(Path(name).name == name, "artifact_name_required")
        fd = os.open(
            folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    return {
        "prepared_only": True,
        "installed": False,
        "artifact_sha256": {name: sha(raw) for name, raw in artifacts.items()},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        fd = os.open(arguments.request, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            need(
                stat.S_ISREG(info.st_mode)
                and info.st_uid in {0, os.getuid()}
                and not info.st_mode & 0o077
                and info.st_size <= 524288,
                "private_request_required",
            )
            request = json.load(stream)
        request["original_overlay"] = request["original_overlay"].encode()
        print(
            json.dumps(
                write_private_bundle(arguments.output, prepare(**request)),
                sort_keys=True,
            )
        )
    except Exception as error:
        # Inputs can contain DB passwords. Never emit their values or tracebacks.
        print(
            json.dumps(
                {
                    "prepared_only": True,
                    "installed": False,
                    "error_type": type(error).__name__,
                }
            )
        )
        raise SystemExit(2)
