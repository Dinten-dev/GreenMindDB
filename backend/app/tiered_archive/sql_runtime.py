"""Explicit SQL-pilot CLI wiring; no scheduler or activation side effects."""

import hashlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.snapshot_api import SnapshotAPI, private_token

from . import parquet, runtime
from .ledger import Ledger, _private_read
from .recovery import restore_url
from .restore_inputs import frozen
from .source import Source
from .storage import Store


def _need(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


class ScopedReadStore(Store):
    """Same archive identity, but no publishing through this SQL-read API."""

    def publish(self, *_args, **_kwargs):
        raise ArchiveBlocked("SQL verification store is read-only")


def _private_file(path, sha, maximum):
    path = Path(path)
    before = frozen(path)
    raw = _private_read(path, maximum)
    _need(
        frozen(path) == before and hashlib.sha256(raw).hexdigest() == sha,
        "Pinned root-private SQL profile differs",
    )
    return raw


def _read_only_request(request):
    _need(request.method == "GET", "Snapshot operator client permits GET only")


def fence_plan(identity):
    """Read the independent catalogue and print a non-executable DDL plan."""
    from .sql_deletion_guard import prepare_fence

    ledger = Ledger(runtime.state_root(), read_only=True)
    try:
        receipt = ledger.get(identity)
        _need(
            type(receipt) is dict and receipt.get("candidate") == identity,
            "Known exact SQL candidate required",
        )
        candidate = runtime.candidate_from_json(receipt["source"])
        _need(candidate.identity == identity, "SQL candidate identity differs")
        result = prepare_fence(candidate)
        return {
            "status": "REVIEW_ONLY",
            "identity": identity,
            "fence_contract_sha256": result["fence_contract_sha256"],
            "ddl": result["ddl"],
            "source_accesses": 0,
        }
    finally:
        ledger.close()


def evict(
    identity,
    *,
    restore_profile_file,
    restore_profile_sha256,
    proof_file,
    proof_sha256,
    authorization_file,
    authorization_sha256,
    snapshot_token_file,
    snapshot_token_sha256,
    operation="evict",
    registry_file=None,
    registry_sha256=None,
    binding_path=None,
    binding_sha256=None,
    dba_identity_path=None,
    dba_identity_sha256=None,
    producer_sha256=None,
    challenge_file=None,
    frame_file=None,
):
    """Requires caller-held archive/source lease and independently approved pins."""
    from app.raw_archive.remote_lease import require_bound_source_lease

    from . import sql_deletion, sql_deletion_guard, sql_deletion_scope, sql_deletion_v3

    _need(
        operation in {"evict", "evict-v3", "source-approval-v3", "scope-bootstrap", "scope-reconcile", "scope-bootstrap-v3", "scope-reconcile-v3"}, "Exact SQL operation required"
    )
    enabled = (
        "TIERED_ARCHIVE_SQL_V3_SCOPE_ENABLED" if operation in {"scope-bootstrap-v3", "scope-reconcile-v3"}
        else "TIERED_ARCHIVE_SQL_V3_CLI_ENABLED" if operation in {"evict-v3", "source-approval-v3"}
        else "TIERED_ARCHIVE_SQL_CLI_ENABLED" if operation == "evict"
        else "TIERED_ARCHIVE_SQL_SCOPE_ENABLED"
    )
    _need(
        os.geteuid() == 0 and os.getenv(enabled) == "true",
        "Explicit root SQL operation enablement required",
    )
    require_bound_source_lease()
    raw_url = _private_file(restore_profile_file, restore_profile_sha256, 8192)
    try:
        url = restore_url(raw_url.decode("utf-8").strip())
    except (ValueError, UnicodeError) as exc:
        raise ArchiveBlocked("Invalid dedicated SQL restore profile") from exc
    token_raw = _private_file(snapshot_token_file, snapshot_token_sha256, 4096)
    token = private_token(Path(snapshot_token_file))
    _need(token_raw.decode("utf-8").strip() == token, "Snapshot read-only profile changed")
    with runtime.worker(deleting=True) as (ledger, destination, checkpoint):
        receipt = ledger.get(identity)
        _need(
            type(receipt) is dict
            and receipt.get("candidate") == identity
            and receipt.get("destination") == destination.identity,
            "Exact archived SQL receipt required",
        )
        candidate = runtime.candidate_from_json(receipt["source"])
        _need(
            candidate.identity == identity
            and candidate.environment == "production"
            and candidate.source == "gateway"
            and candidate.dataset == "ingest_log"
            and type(candidate.rows) is int
            and 0 < candidate.rows <= 32,
            "Only exact bounded Gateway ingest-log pilot supported",
        )
        references = receipt.get("files")
        _need(
            type(references) is list
            and len(references) == 1
            and references[0].get("format") == "parquet"
            and references[0].get("rows") == candidate.rows
            and references[0].get("content_sha256") == candidate.content_sha256,
            "Exact independently verified SQL archive part required",
        )
        # Existing transport identity is unchanged. SQL code only exposes GET.
        store = ScopedReadStore(destination.transport)
        source_engine = runtime.engine(candidate.source, writing=True)
        restore_engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 3})
        try:
            source_url = source_engine.url
            _need(
                url.database != source_url.database
                and not (url.host == source_url.host and url.port == source_url.port),
                "Restore target must be separate from Source",
            )
            adapter = Source(
                source_engine, candidate.source, candidate.environment, checkpoint=checkpoint
            )
            with tempfile.TemporaryDirectory(prefix="sql-pilot-", dir=ledger.root) as folder:
                path = Path(folder) / "source.parquet"
                ref = references[0]
                checkpoint()
                store.download(ref["key"], path, ref["bytes"])
                adapter.expected = list(parquet.read(path, ref, checkpoint=checkpoint))
                _need(len(adapter.expected) == candidate.rows, "SQL archive rows differ")
                with httpx.Client(
                    headers={"Authorization": "Bearer " + token},
                    timeout=httpx.Timeout(8, connect=3),
                    trust_env=False,
                    event_hooks={"request": [_read_only_request]},
                ) as client:
                    provider = SnapshotAPI(
                        client,
                        int(os.environ["TIERED_ARCHIVE_BOX_ID"]),
                        os.environ["RAW_ARCHIVE_SFTP_USER"],
                        os.environ["RAW_ARCHIVE_SFTP_HOST"],
                    )
                    guard_factory = (sql_deletion_guard.build_guard_v3 if operation in {"evict-v3", "source-approval-v3", "scope-bootstrap-v3", "scope-reconcile-v3"} else sql_deletion_guard.build_guard)
                    proof = guard_factory(
                        candidate,
                        adapter,
                        ledger,
                        store,
                        provider,
                        restore_engine,
                        evidence_path=proof_file,
                        evidence_sha256=proof_sha256,
                        checkpoint=checkpoint,
                    )
                    action = {
                        "evict": sql_deletion.evict,
                        "evict-v3": sql_deletion_v3.evict,
                        "source-approval-v3": sql_deletion_v3.stage_source_approval,
                        "scope-bootstrap": sql_deletion_scope.bootstrap,
                        "scope-reconcile": sql_deletion_scope.reconcile,
                        "scope-bootstrap-v3": sql_deletion_v3.scope_transition,
                        "scope-reconcile-v3": sql_deletion_v3.scope_transition,
                    }[operation]
                    extra = {}
                    if operation == "source-approval-v3":
                        extra = {"binding_path": binding_path, "binding_sha256": binding_sha256,
                                 "dba_identity_path": dba_identity_path, "dba_identity_sha256": dba_identity_sha256,
                                 "producer_sha256": producer_sha256, "challenge_file": challenge_file, "frame_file": frame_file}
                    elif operation not in {"evict", "evict-v3"}:
                        import json

                        from app.raw_archive.read_only_box import configured_catalog_read_only_box

                        _need(
                            registry_file is not None and registry_sha256 is not None,
                            "Pinned independent SQL registry required",
                        )
                        reference = json.loads(_private_file(registry_file, registry_sha256, 16384))
                        readonly = configured_catalog_read_only_box()
                        _need(
                            readonly is not None and readonly.identity == destination.identity,
                            "Distinct matching SQL registry verifier required",
                        )
                        try:
                            extra["registry_evidence"] = (
                                sql_deletion_scope.verify_registry_snapshot(
                                    ledger,
                                    Store(readonly),
                                    reference,
                                    environment=candidate.environment,
                                    checkpoint=checkpoint,
                                )
                            )
                        finally:
                            readonly.close()
                        if operation in {"scope-bootstrap-v3", "scope-reconcile-v3"}:
                            extra["bootstrap"] = operation == "scope-bootstrap-v3"
                    result = action(
                        candidate,
                        adapter,
                        ledger,
                        authorization_path=authorization_file,
                        authorization_sha256=authorization_sha256,
                        runtime_sha256=runtime.runtime_sha256(),
                        bindings_sha256=runtime.bindings_sha256(store),
                        checkpoint=checkpoint,
                        clock=lambda: datetime.now(UTC),
                        proof_guard=proof,
                        **extra,
                    )
                    return {
                        "status": result["state"] if operation in {"evict", "evict-v3"} else result["status"],
                        "identity": candidate.identity,
                        **({"epoch": result["epoch"]} if operation in {"scope-bootstrap", "scope-reconcile", "scope-bootstrap-v3", "scope-reconcile-v3"} else {}),
                        **({"proof_sha256": result["proof_sha256"]} if operation in {"scope-bootstrap-v3", "scope-reconcile-v3"} else {}),
                    }
        finally:
            source_engine.dispose()
            restore_engine.dispose()


def prepare_sql_registry():
    """Publish and independently restore the actual current SQL budget catalog."""
    from app.raw_archive.read_only_box import configured_catalog_read_only_box
    from app.raw_archive.remote_lease import require_bound_source_lease

    from . import registry_backup, registry_v5, sql_deletion_scope

    _need(
        os.geteuid() == 0 and os.getenv("TIERED_ARCHIVE_SQL_SCOPE_ENABLED") == "true",
        "Explicit root SQL scope enablement required",
    )
    require_bound_source_lease()
    with runtime.worker() as (ledger, destination, checkpoint):
        db = ledger.db
        existing = sql_deletion_scope._table(db, "sql_deletion_pilot")
        state = (
            db.execute("SELECT state FROM sql_deletion_scope WHERE id=1").fetchone()
            if sql_deletion_scope._table(db, "sql_deletion_scope")
            else None
        )
        _need(
            not sql_deletion_scope._table(db, "sql_deletion_transition"),
            "Unresolved SQL scope transition",
        )
        if not existing:
            _need(
                state is None and not sql_deletion_scope.inventory(ledger)["receipts"],
                "Unknown or restored SQL budget cannot be created as empty",
            )
            with db:
                db.execute(registry_v5.DDL["sql_deletion_pilot"])
        else:
            registry_v5.schema(db, "sql_deletion_pilot")
        readonly = configured_catalog_read_only_box()
        _need(
            readonly is not None and readonly.identity == destination.identity,
            "Separate SQL registry verifier required",
        )
        verifier = Store(readonly)

        class ReadbackPublisher:
            identity = destination.identity

            def publish(self, key, path):
                return destination.publish(key, path)

            def download(self, key, path, size):
                return verifier.download(key, path, size)

        try:
            reference = registry_backup.backup(ledger, ReadbackPublisher(), checkpoint=checkpoint)
            proof = sql_deletion_scope.verify_registry_snapshot(
                ledger,
                verifier,
                reference,
                environment=runtime.environment(),
                checkpoint=checkpoint,
            )
            import json

            canonical_reference = json.dumps(
                reference, sort_keys=True, separators=(",", ":")
            ).encode()
            return {
                "status": "SQL_REGISTRY_VERIFIED",
                "reference": reference,
                "reference_sha256": proof.reference_sha,
                "reference_file_sha256": hashlib.sha256(canonical_reference).hexdigest(),
                "reference_file_encoding": "canonical-json-no-newline",
                "sql_scope_enabled": False,
            }
        finally:
            readonly.close()


def _await_source_frame(challenge_file, frame_file, action, payload, checkpoint, *, seconds=45, maximum=1024 * 1024):
    """Exchange a fresh lease challenge with an independently managed root bridge."""
    import json
    import stat
    import time

    from app.raw_archive.scientific_scope_control import current_source_session

    from .restore_inputs import protected_parents

    _need(type(maximum) is int and 0 < maximum <= 6 * 1024 * 1024, "Bounded Source frame required")
    challenge, frame = Path(challenge_file), Path(frame_file)
    _need(
        challenge.is_absolute()
        and frame.is_absolute()
        and challenge != frame
        and challenge.parent == frame.parent
        and not os.path.lexists(challenge)
        and not os.path.lexists(frame),
        "New private Source challenge and frame required",
    )
    protected_parents(challenge)
    directory = challenge.parent.lstat()
    _need(
        stat.S_ISDIR(directory.st_mode)
        and directory.st_uid == 0
        and stat.S_IMODE(directory.st_mode) == 0o700,
        "Root-private Source bridge directory required",
    )
    session = current_source_session()
    message = {
        "format": "greenmind.science-source-challenge.v1",
        "action": action,
        "lease_session": session,
        "payload": payload,
    }
    raw = (json.dumps(message, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(challenge, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    created = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as body:
            body.write(raw)
            body.flush()
            os.fsync(body.fileno())
        directory_fd = os.open(challenge.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        deadline = time.monotonic() + min(seconds, 45)
        while True:
            checkpoint()
            _need(current_source_session() == session, "Source lease generation changed")
            if os.path.lexists(frame):
                before = frozen(frame)
                raw_frame = _private_read(frame, maximum)
                _need(frozen(frame) == before, "Source bridge frame changed")
                return hashlib.sha256(raw_frame).hexdigest()
            _need(
                time.monotonic() < deadline, "Source root bridge frame not received within deadline"
            )
            time.sleep(0.1)
    finally:
        if os.path.lexists(challenge):
            current = challenge.lstat()
            _need(
                stat.S_ISREG(current.st_mode)
                and (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino),
                "Source challenge generation changed",
            )
            challenge.unlink()


def science_bootstrap(
    new_root,
    *,
    source_binding_file,
    source_binding_sha256,
    metadata_dir,
    capture_sha256,
    index_file,
    index_sha256,
    challenge_file,
    frame_file,
):
    """Create a new Science scope using a same-lease Source root challenge."""
    from app.raw_archive.remote_lease import require_bound_source_lease
    from app.raw_archive.scientific_scope_control import bootstrap_science

    _need(
        os.geteuid() == 0 and os.getenv("TIERED_ARCHIVE_SCIENCE_BOOTSTRAP_ENABLED") == "true",
        "Explicit root Science bootstrap enablement required",
    )
    require_bound_source_lease()
    with runtime.worker() as (authority, _destination, checkpoint):
        frame_sha = _await_source_frame(
            challenge_file,
            frame_file,
            "capture-index",
            {"capture_sha256": capture_sha256, "index_sha256": index_sha256},
            checkpoint,
        )
        return bootstrap_science(
            new_root,
            authority,
            source_binding_path=source_binding_file,
            source_binding_sha256=source_binding_sha256,
            metadata_dir=metadata_dir,
            capture_sha256=capture_sha256,
            index_file=index_file,
            index_sha256=index_sha256,
            frame_path=frame_file,
            frame_sha256=frame_sha,
            checkpoint=checkpoint,
        )


def science_reconcile(
    target_root,
    position,
    *,
    source_binding_file,
    source_binding_sha256,
    key_file,
    challenge_file,
    frame_file,
):
    """One bounded remote GET-only Science reconciliation step under existing locks."""
    import stat

    from app.raw_archive.read_only_box import ReadOnlyBox, configured_read_only_box
    from app.raw_archive.remote_lease import require_bound_source_lease
    from app.raw_archive.scientific_reconcile import reconcile_step

    from .backup import key_from_file
    from .restore_inputs import protected_parents

    _need(
        os.geteuid() == 0 and os.getenv("TIERED_ARCHIVE_SCIENCE_RECONCILE_ENABLED") == "true",
        "Explicit root Science reconciliation enablement required",
    )
    require_bound_source_lease()
    target_root = Path(target_root)
    _need(
        target_root.is_absolute()
        and target_root.resolve(strict=False) == target_root
        and target_root.is_dir()
        and not target_root.is_symlink(),
        "Existing isolated Science ledger required",
    )
    protected_parents(target_root)
    info = target_root.lstat()
    _need(
        info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700,
        "Root-private Science ledger required",
    )
    db = target_root / "tiered.sqlite3"
    db_info = db.lstat()
    _need(
        stat.S_ISREG(db_info.st_mode)
        and db_info.st_uid == 0
        and db_info.st_nlink == 1
        and stat.S_IMODE(db_info.st_mode) == 0o600,
        "Exact private restored Science journal required",
    )
    key = key_from_file(Path(key_file))
    readonly_key = os.getenv("RAW_ARCHIVE_READ_ONLY_SFTP_KEY", "")
    _need(readonly_key, "Explicit read-only credential required")
    frozen(Path(readonly_key))
    readonly = configured_read_only_box()
    _need(
        type(readonly) is ReadOnlyBox, "Separately configured read-only archive subaccount required"
    )
    try:
        with runtime.worker() as (authority, destination, checkpoint):
            _need(
                target_root.resolve() != authority.root.resolve()
                and readonly.identity == destination.identity,
                "Separate restored ledger and matching read-only archive required",
            )
            verifier = ScopedReadStore(readonly)
            frame_sha = _await_source_frame(
                challenge_file, frame_file, "lease-lock", {}, checkpoint, seconds=20
            )
            target = Ledger(target_root)
            try:
                return reconcile_step(
                    target,
                    authority,
                    verifier,
                    key,
                    target_root,
                    position,
                    source_binding_path=source_binding_file,
                    source_binding_sha256=source_binding_sha256,
                    lease_frame_path=frame_file,
                    lease_frame_sha256=frame_sha,
                    checkpoint=checkpoint,
                )
            finally:
                target.close()
    finally:
        readonly.close()
