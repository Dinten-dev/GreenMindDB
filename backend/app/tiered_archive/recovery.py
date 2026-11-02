"""Archive-only restoration into a new directory; originals are never replaced."""

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked, checksum

from . import parquet
from .codec import encode
from .pipeline import candidate_json, verify_file
from .runtime import candidate_from_json


@dataclass(frozen=True)
class RestoreTLSFilePin:
    """Explicit immutable Archive Root artifact generation; never auto-discovered."""

    path: Path
    device: int
    inode: int
    bytes: int
    modified_ns: int
    changed_ns: int
    sha256: str


@dataclass(frozen=True)
class RestoreTLSTarget:
    database: str
    database_oid: int
    role: str
    role_oid: int
    port: int
    server_address: str
    postmaster_start: str
    marker_sha256: str

    def identity(self):
        return {key: value for key, value in asdict(self).items() if key != "marker_sha256"}


@dataclass(frozen=True)
class RestoreTLSBinding:
    """Real Root artifacts plus the existing coordinator's fresh Source authority.

    guard(binding, actual_source_artifact_bytes) must validate the actual Source
    lifecycle/generation and its server-CA/SQL-target bindings, or raise. The API
    does not manufacture or reinterpret Source proofs. SHA/Root ownership alone
    never supplies that authority. The guard must remain cheap enough for the
    caller's existing checkpoints; Source resource monitoring stays its owner's job.
    """

    source_target_proof: RestoreTLSFilePin
    root_certificate: RestoreTLSFilePin
    client_certificate: RestoreTLSFilePin
    client_key: RestoreTLSFilePin
    target: RestoreTLSTarget
    guard: Callable[["RestoreTLSBinding", bytes], None] = field(repr=False, compare=False)


_TLS_ROOT = Path("/var/lib/greenmind")
_TLS_ARCHIVE_MACHINE = "b09421f9d8d4e1c392da9b1cca2ccb2e5d710ae73cf7051a615c3dc040bbd0a9"


def _tls_need(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


def _tls_archive_authority():
    _tls_need(
        os.geteuid() == 0
        and hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest()
        == _TLS_ARCHIVE_MACHINE,
        "TLS restore requires the Archive Root coordinator",
    )


def _tls_read_file(pin):
    _tls_need(type(pin) is RestoreTLSFilePin, "Typed Root TLS file pin required")
    path = pin.path
    _tls_need(
        isinstance(path, Path)
        and path.is_absolute()
        and ".." not in path.parts
        and path.is_relative_to(_TLS_ROOT)
        and path != _TLS_ROOT
        and all(
            type(value) is int and value > 0
            for value in (pin.device, pin.inode, pin.bytes, pin.modified_ns, pin.changed_ns)
        )
        and pin.bytes <= 256 * 1024
        and isinstance(pin.sha256, str)
        and re.fullmatch(r"[a-f0-9]{64}", pin.sha256),
        "Exact bounded Archive Root TLS artifact required",
    )
    parent = path.parent
    while True:
        info = parent.lstat()
        _tls_need(
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == 0
            and not info.st_mode & 0o022
            and (parent == _TLS_ROOT or stat.S_IMODE(info.st_mode) == 0o700),
            "Private Archive Root TLS directory required",
        )
        if parent == _TLS_ROOT:
            break
        parent = parent.parent

    def identity(info):
        _tls_need(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == info.st_gid == 0
            and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o400,
            "Immutable private Archive Root TLS file required",
        )
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    expected = (pin.device, pin.inode, pin.bytes, pin.modified_ns, pin.changed_ns)
    _tls_need(identity(path.lstat()) == expected, "Archive Root TLS file generation changed")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        _tls_need(
            identity(os.fstat(stream.fileno())) == expected,
            "Archive Root TLS file generation changed",
        )
        raw = stream.read(256 * 1024 + 1)
        _tls_need(
            identity(os.fstat(stream.fileno())) == expected,
            "Archive Root TLS file generation changed",
        )
    _tls_need(
        identity(path.lstat()) == expected
        and len(raw) == pin.bytes
        and hashlib.sha256(raw).hexdigest() == pin.sha256,
        "Archive Root TLS artifact SHA or generation changed",
    )
    return raw


def _validate_restore_tls(binding, url):
    if binding is None:
        return {}
    _tls_archive_authority()
    _tls_need(
        type(binding) is RestoreTLSBinding and type(binding.target) is RestoreTLSTarget,
        "Typed explicit restore TLS binding required",
    )
    target = binding.target
    _tls_need(
        url.drivername == "postgresql+psycopg2"
        and url.host == "127.0.0.1"
        and url.port == target.port == 19442
        and type(target.port) is int
        and url.username == target.role
        and bool(url.password)
        and not url.query
        and isinstance(target.database, str)
        and url.database == target.database
        and re.fullmatch(
            r"gm_native_restore_[a-f0-9]{12,32}_(gateway|direct)", target.database or ""
        )
        and isinstance(target.role, str)
        and re.fullmatch(r"[a-z][a-z0-9_]{0,62}", target.role)
        and all(
            type(value) is int and value > 0 for value in (target.database_oid, target.role_oid)
        )
        and isinstance(target.server_address, str)
        and target.server_address in {"127.0.0.1", "127.0.0.1/32"}
        and isinstance(target.postmaster_start, str)
        and re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?[+-][0-9]{2}(?::[0-9]{2})?",
            target.postmaster_start,
        )
        and isinstance(target.marker_sha256, str)
        and re.fullmatch(r"[a-f0-9]{64}", target.marker_sha256)
        and callable(binding.guard),
        "Pinned isolated loopback19442 TLS target required",
    )
    pins = (
        binding.source_target_proof,
        binding.root_certificate,
        binding.client_certificate,
        binding.client_key,
    )
    _tls_need(
        all(type(pin) is RestoreTLSFilePin and isinstance(pin.path, Path) for pin in pins)
        and len({pin.path for pin in pins}) == 4,
        "Distinct typed Root TLS artifacts required",
    )
    source = _tls_read_file(binding.source_target_proof)
    for pin in pins[1:]:
        _tls_read_file(pin)
    # A caller-provided actual Root coordinator, never a backend-generated proof.
    _tls_need(binding.guard(binding, source) is None, "Fresh Source target authority required")
    # The fresh observer cannot silently replace an artifact during its check.
    for pin in pins:
        _tls_read_file(pin)
    return {
        "hostaddr": "127.0.0.1",
        "sslmode": "verify-full",
        "sslrootcert": str(binding.root_certificate.path),
        "sslcert": str(binding.client_certificate.path),
        "sslkey": str(binding.client_key.path),
    }


def restore_tls_checkpoint(binding, url, checkpoint):
    if binding is None:
        return checkpoint
    _validate_restore_tls(binding, url)

    def checked():
        checkpoint()
        _validate_restore_tls(binding, url)

    return checked


def restore_tls_env(binding, url):
    options = _validate_restore_tls(binding, url)
    return (
        {
            key: options[value]
            for key, value in (
                ("PGHOSTADDR", "hostaddr"),
                ("PGSSLMODE", "sslmode"),
                ("PGSSLROOTCERT", "sslrootcert"),
                ("PGSSLCERT", "sslcert"),
                ("PGSSLKEY", "sslkey"),
            )
        }
        if options
        else {}
    )


def restore_tls_connect_args(binding, url):
    return _validate_restore_tls(binding, url)


def restore_tls_identity(db, engine, binding, *, checkpoint):
    if binding is None:
        return
    from sqlalchemy import text

    from .parent_companion import _target

    _validate_restore_tls(binding, engine.url)

    def target_guard():
        _validate_restore_tls(binding, engine.url)

    _target(
        db,
        engine,
        binding.target.identity(),
        binding.target.marker_sha256,
        binding.target.database.rsplit("_", 1)[1],
        checkpoint,
        target_guard,
    )
    encrypted = db.execute(
        text("SELECT ssl AND bits>=128 FROM pg_stat_ssl WHERE pid=pg_backend_pid()")
    ).scalar_one()
    _tls_need(encrypted is True, "Pinned restore connection requires actual PostgreSQL TLS")


def restore_tls_engine_guard(engine, binding, url, *, checkpoint):
    if binding is None:
        return
    from sqlalchemy import event

    def before_connect(dialect, connection_record, arguments, parameters):
        checkpoint()
        expected = _validate_restore_tls(binding, url)
        _tls_need(
            all(parameters.get(key) == value for key, value in expected.items())
            and parameters.get("host") == "127.0.0.1"
            and parameters.get("port") == 19442
            and parameters.get("dbname") == binding.target.database
            and parameters.get("user") == binding.target.role
            and parameters.get("password") == url.password,
            "Restore driver TLS or endpoint parameters changed",
        )

    event.listen(engine, "do_connect", before_connect)


def restore_tls_guard_sql(binding, url):
    if binding is None:
        return ""
    _validate_restore_tls(binding, url)
    target = binding.target

    # Fixed typed identifiers/numbers; E literals quote the one live timestamp.
    def literal(value):
        return "E'" + value.replace("\\", "\\\\").replace("'", "''") + "'"

    return (
        "SET LOCAL search_path=pg_catalog;\nDO $greenmind_tls_target$ BEGIN IF "
        "current_database()<>"
        + literal(target.database)
        + " OR (SELECT oid FROM pg_database WHERE datname=current_database())<>"
        + str(target.database_oid)
        + " OR (SELECT datdba FROM pg_database WHERE datname=current_database())<>"
        + str(target.role_oid)
        + " OR current_user<>"
        + literal(target.role)
        + " OR current_user<>session_user"
        + " OR (SELECT oid FROM pg_roles WHERE rolname=current_user)<>"
        + str(target.role_oid)
        + " OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname=current_user AND(NOT rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))"
        + " OR inet_server_port() IS DISTINCT FROM 19442"
        + " OR inet_server_addr()::text IS DISTINCT FROM "
        + literal(target.server_address)
        + " OR pg_postmaster_start_time()::text<>"
        + literal(target.postmaster_start)
        + " OR current_setting('server_version_num')<>'150010'"
        + " OR pg_is_in_recovery() OR current_setting('session_replication_role')<>'origin'"
        + " OR has_database_privilege('public',current_database(),'CONNECT')"
        + " OR has_database_privilege('public',current_database(),'TEMPORARY')"
        + " OR encode(sha256(convert_to(coalesce((SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()),''),'UTF8')),'hex')<>"
        + literal(target.marker_sha256)
        + " OR NOT EXISTS(SELECT 1 FROM pg_stat_ssl WHERE pid=pg_backend_pid() AND ssl AND bits>=128)"
        + " THEN RAISE EXCEPTION 'Pinned TLS restore target changed'; END IF; END; $greenmind_tls_target$;\n"
    )


def restore_tls_source(binding, source):
    if binding is not None:
        _tls_need(
            binding.target.database.endswith("_" + source),
            "TLS target belongs to another restore source",
        )


def restore_tls_evidence(binding, url=None):
    if binding is None:
        return {}
    _tls_need(url is not None, "Pinned TLS receipt target required")
    _validate_restore_tls(binding, url)
    from .codec import digest

    return {
        "tls_verified_target_sha256": digest(asdict(binding.target)),
        "tls_source_target_proof_sha256": binding.source_target_proof.sha256,
        "tls_root_certificate_sha256": binding.root_certificate.sha256,
    }


def restore(identity, output, ledger, store, *, checkpoint):
    receipt = ledger.get(identity)
    if not receipt or receipt["state"] not in {"verified", "deleting", "evicted"}:
        raise ArchiveBlocked("Verified archive receipt required for restoration")
    if not output.is_absolute() or output.exists() or output.is_symlink():
        raise ArchiveBlocked("Restore requires a new isolated directory")
    output.mkdir(mode=0o700, parents=False)
    candidate = candidate_from_json(receipt["source"])
    reference = receipt["manifest"]
    manifest = output / "manifest.json"
    store.download(reference["key"], manifest, reference["bytes"])
    verify_file(manifest, reference, checkpoint)
    data = json.loads(manifest.read_text())
    if (
        data["identity"] != identity
        or data["files"] != receipt["files"]
        or data["candidate"] != candidate_json(candidate)
    ):
        raise ArchiveBlocked("Restored bundle manifest mismatch")
    rows = 0
    for number, reference in enumerate(receipt["files"]):
        checkpoint()
        target = output / f"{number:04d}.{reference['format']}"
        store.download(reference["key"], target, reference["bytes"])
        os.chmod(target, 0o600)
        verify_file(target, reference, checkpoint)
        if reference["format"] == "parquet":
            with (output / f"{number:04d}.lossless.jsonl").open("xb") as body:
                os.chmod(body.name, 0o600)
                for row in parquet.read(target, reference, checkpoint=checkpoint):
                    body.write(encode(row) + b"\n")
                    rows += 1
                body.flush()
                os.fsync(body.fileno())
    result = {
        "identity": identity,
        "rows": rows,
        "files": len(receipt["files"]),
        "manifest_sha256": checksum(manifest),
        "source_accesses": 0,
    }
    report = output / "restore-result.json"
    report.write_text(json.dumps(result, sort_keys=True))
    os.chmod(report, 0o600)
    return result


def restore_database(
    folder,
    database_url,
    manifest_sha256,
    *,
    checkpoint,
    tls_binding: RestoreTLSBinding | None = None,
):
    """Restore exact typed rows into an empty table in an isolated local DB."""
    from sqlalchemy import MetaData, Table, and_, create_engine, func, select, text

    from .source import DATASETS

    url = restore_url(database_url)
    checkpoint = restore_tls_checkpoint(tls_binding, url, checkpoint)
    manifest_path = folder / "manifest.json"
    if checksum(manifest_path) != manifest_sha256:
        raise ArchiveBlocked("Restore manifest changed")
    manifest = json.loads(manifest_path.read_text())
    candidate = candidate_from_json(manifest["candidate"])
    restore_tls_source(tls_binding, candidate.source)
    if candidate.dataset not in DATASETS:
        raise ArchiveBlocked("Use metadata/WAV restore for this bundle")
    spec = DATASETS[candidate.dataset]
    if candidate.source != spec.source or manifest.get("identity") != candidate.identity:
        raise ArchiveBlocked("Restore source identity mismatch")
    parts = manifest["files"]
    if (
        not 1 <= len(parts) <= 128
        or any(part["format"] != "parquet" for part in parts)
        or sum(part["rows"] for part in parts) != candidate.rows
        or sum(part["decoded_bytes"] for part in parts) > parquet.MAX_DECODED_BYTES
    ):
        raise ArchiveBlocked("Complete bounded SQL restore parts required")

    def rows():
        for number, reference in enumerate(parts):
            for envelope in parquet.read(
                folder / f"{number:04d}.parquet", reference, checkpoint=checkpoint
            ):
                if envelope["organization"] != candidate.organization:
                    raise ArchiveBlocked("SQL restore organization mismatch")
                if set(envelope["body"]) != {"row", "context"}:
                    raise ArchiveBlocked("Unexpected SQL restore envelope")
                checkpoint()
                yield envelope["body"]

    checkpoint()
    engine = create_engine(
        url,
        pool_size=1,
        max_overflow=0,
        **(
            {"connect_args": restore_tls_connect_args(tls_binding, url)}
            if tls_binding is not None
            else {}
        ),
    )
    restore_tls_engine_guard(engine, tls_binding, url, checkpoint=checkpoint)
    count = 0
    try:
        with engine.begin() as db:
            restore_tls_identity(db, engine, tls_binding, checkpoint=checkpoint)
            db.execute(text("SET LOCAL lock_timeout='150ms'"))
            db.execute(text("SET LOCAL statement_timeout='8000ms'"))
            metadata = MetaData()
            table = Table(spec.table, metadata, autoload_with=db)
            children = {}
            if spec.table == "direct_chunk":
                children = {
                    name: Table(name, metadata, autoload_with=db)
                    for name in ("direct_chunk_payload_ref", "direct_piece")
                }
            # Fixed allowlisted names only. Keep concurrent writers out until
            # the final typed readback and transaction commit have completed.
            for target in (table, *children.values()):
                db.execute(text(f'LOCK TABLE "{target.name}" IN ACCESS EXCLUSIVE MODE'))
                if db.execute(select(func.count()).select_from(target)).scalar_one() != 0:
                    raise ArchiveBlocked("Restore never merges into a populated target table")
            child_counts = dict.fromkeys(children, 0)
            for body in rows():
                db.execute(table.insert().values(**body["row"]))
                count += 1
                if children:
                    for name, values in direct_children(body).items():
                        for row in values:
                            db.execute(children[name].insert().values(**row))
                            child_counts[name] += 1
            if count != candidate.rows:
                raise ArchiveBlocked("Database restoration row count mismatch")
            if db.execute(select(func.count()).select_from(table)).scalar_one() != count:
                raise ArchiveBlocked("Stored PostgreSQL row count differs from archive")
            for name, child in children.items():
                if (
                    db.execute(select(func.count()).select_from(child)).scalar_one()
                    != child_counts[name]
                ):
                    raise ArchiveBlocked("Stored Direct dependency count differs from archive")
            # Re-read every part without retaining all payloads in RAM. Check
            # the database values, including changes made by database triggers.
            for body in rows():
                expected = body["row"]
                actual = (
                    db.execute(
                        select(table).where(
                            and_(*(table.c[key] == expected[key] for key in spec.keys))
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if actual is None or encode(dict(actual)) != encode(expected):
                    raise ArchiveBlocked("Stored PostgreSQL values differ from archive")
                if children:
                    for name, expected_rows in direct_children(body).items():
                        actual_rows = db.execute(
                            select(children[name]).where(
                                children[name].c.chunk_id == expected["id"]
                            )
                        ).mappings()
                        if sorted(encode(dict(row)) for row in actual_rows) != sorted(
                            encode(row) for row in expected_rows
                        ):
                            raise ArchiveBlocked("Stored Direct dependencies differ from archive")
        return {
            "restored_rows": count,
            "typed_values_verified": True,
            "source_accesses": 0,
            **restore_tls_evidence(tls_binding, url),
        }
    finally:
        engine.dispose()


def restore_url(database_url):
    """Reject libpq host/service overrides before creating any connection."""
    import re

    from sqlalchemy.engine import make_url

    url = make_url(database_url)
    if (
        url.drivername not in {"postgresql", "postgresql+psycopg2"}
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or not re.fullmatch(r"[a-z][a-z0-9_]*_restore_[a-z0-9_]+", url.database or "")
        or not url.username
        or url.query
    ):
        raise ArchiveBlocked(
            "Only a dedicated local _restore_ database without overrides may be populated"
        )
    return url


def archived_direct_original(body):
    """Verify and return the exact chunk bytes stored in an archive context."""
    import hashlib

    row, context = body["row"], body["context"]
    if not isinstance(context, dict) or not isinstance(context.get("original_payload"), bytes):
        raise ArchiveBlocked("Archived Direct original bytes are missing")
    payload = context["original_payload"]
    if (
        type(row.get("payload_bytes")) is not int
        or len(payload) != row["payload_bytes"]
        or not isinstance(row.get("payload_sha256"), str)
        or hashlib.sha256(payload).hexdigest() != row["payload_sha256"]
        or (row.get("payload") is not None and bytes(row["payload"]) != payload)
    ):
        raise ArchiveBlocked("Archived Direct original bytes differ from source identity")
    from app.direct.capture_assignment import split
    from app.direct.protocol import parse_metadata

    try:
        config, _ = split(context["session_config"])
        metadata = parse_metadata(
            config
            | {
                "sequence": row["sequence"],
                "first_frame": row["first_frame"],
                "frame_count": row["frame_count"],
                "payload_sha256": row["payload_sha256"],
            }
        )
        if (
            metadata.digest() != row["digest"]
            or len(payload) != metadata.frame_count * metadata.channels * metadata.sample_bits // 8
        ):
            raise ValueError("original protocol identity differs")
        pieces = sorted(context["pieces"], key=lambda piece: piece["payload_offset"])
        expected = list(metadata.pieces())
        if len(pieces) != len(expected) or any(
            piece["chunk_id"] != row["id"]
            or (piece["payload_offset"], piece["frame_count"]) != (bound[3], bound[4])
            for piece, bound in zip(pieces, expected, strict=True)
        ):
            raise ValueError("archived original piece coverage differs")
    except (KeyError, TypeError, ValueError) as error:
        raise ArchiveBlocked("Archived Direct original protocol or pieces differ") from error
    return payload


def direct_children(body):
    context = body["context"]
    # Older test/legacy rows may lack the new context; real source rows with a
    # payload checksum require the independently verified archived original.
    if "payload_sha256" in body["row"] or "original_payload" in context:
        archived_direct_original(body)
    result = {
        "direct_chunk_payload_ref": [context["payload_reference"]]
        if context["payload_reference"] is not None
        else [],
        "direct_piece": context["pieces"],
    }
    if any(row["chunk_id"] != body["row"]["id"] for values in result.values() for row in values):
        raise ArchiveBlocked("Direct dependency belongs to another chunk")
    return result
