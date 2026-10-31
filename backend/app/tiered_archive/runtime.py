"""Explicit worker wiring; reuse existing source isolation and health guards."""

import fcntl
import hashlib
import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked

from .ledger import Ledger
from .policy import Candidate
from .storage import Cache, Store


def state_root():
    root = Path(os.environ.get("TIERED_ARCHIVE_STATE_DIR", "/var/lib/greenmind/tiered-archive"))
    if not root.is_absolute() or root.is_symlink():
        raise ArchiveBlocked("Isolated absolute tiered state required")
    return root


def environment():
    value = os.environ.get("TIERED_ARCHIVE_ENVIRONMENT", "")
    if value not in {"staging", "production"}:
        raise ArchiveBlocked("Explicit tiered environment required")
    if os.environ.get("RAW_ARCHIVE_ENVIRONMENT") != value:
        raise ArchiveBlocked("Raw and tiered environments differ")
    return value


def runtime_sha256():
    root = Path(__file__).resolve().parents[1]
    result = hashlib.sha256()
    # Bind acceptance to the whole deployed backend, including old download
    # routes and adapters, rather than only this module or a mutable git label.
    files = [
        *root.rglob("*.py"),
        *root.rglob("*.sql"),
        root.parent / "requirements.lock",
        root.parent / "requirements-archive.lock",
    ]
    for path in sorted(files):
        result.update(path.relative_to(root.parent).as_posix().encode() + b"\0")
        result.update(path.read_bytes())
    return result.hexdigest()


def bindings_sha256(store):
    from app.raw_archive.runner import source_settings

    from .codec import digest

    return digest(
        {
            "environment": environment(),
            "destination": store.identity,
            "source_work_leases": {
                "enabled": os.getenv("WAV_SOURCE_WORK_LEASES_ENABLED") == "true",
                "coverage_sha256": os.getenv("TIERED_ARCHIVE_WORK_LEASE_COVERAGE_SHA256", ""),
            },
            "sources": {
                source: source_settings(source, environment())["identity"]
                for source in ("gateway", "direct")
            },
        }
    )


def candidate_from_json(value):
    from .policy import utc

    value = dict(value)
    for key in ("start", "end", "settled_at", "late_until"):
        if value.get(key) is not None:
            value[key] = utc(value[key])
    candidate = Candidate(**value)
    candidate.validate()
    return candidate


def reader_resources():
    from app.raw_archive.runner import destination_from_environment

    environment()
    ledger = Ledger(state_root(), read_only=True)
    cache = Cache(Path(os.environ.get("TIERED_ARCHIVE_CACHE_DIR", str(state_root() / "cache"))))
    return ledger, cache, Store(destination_from_environment())


@contextmanager
def worker(*, deleting=False):
    import time

    from app.raw_archive.coordination import lease
    from app.raw_archive.health import SafetyPause
    from app.raw_archive.remote_lease import source_checkpoint
    from app.raw_archive.runner import destination_from_environment

    from .latency import health_probe

    source_checkpoint(deleting=deleting)  # Before any state or source IO.
    if os.getenv("TIERED_ARCHIVE_ENABLED", "false") != "true":
        raise ArchiveBlocked("Tiered worker disabled")
    environment()
    root = state_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        saved = {}
        if deleting:
            limits = {
                "RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB": str(
                    max(1024, int(os.environ.get("RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB", "1024")))
                ),
                "RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB": str(
                    max(960, int(os.environ.get("RAW_ARCHIVE_SOURCE_MIN_MEMORY_MIB", "960")))
                ),
                "RAW_ARCHIVE_MAX_HOST_LOAD": str(
                    min(2, float(os.environ.get("RAW_ARCHIVE_MAX_HOST_LOAD", "2")))
                ),
                "RAW_ARCHIVE_SOURCE_MAX_LOAD": str(
                    min(2, float(os.environ.get("RAW_ARCHIVE_SOURCE_MAX_LOAD", "2")))
                ),
            }
            for key, value in limits.items():
                saved[key] = os.environ.get(key)
                os.environ[key] = value
        try:
            health = health_probe("copy")
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        ledger = Ledger(root)
        try:
            last_check = [0.0]

            def checkpoint():
                source_checkpoint(deleting=deleting)
                if (root / "PAUSE").exists() or (root / "control/PAUSE").exists():
                    raise SafetyPause("manual_pause")
                if time.monotonic() - last_check[0] < 1:
                    return
                if not health():
                    raise SafetyPause("receiver_or_archive_resources")
                last_check[0] = time.monotonic()

            checkpoint()
            if deleting:
                time.sleep(1)
                last_check[0] = 0
                checkpoint()
            transport = destination_from_environment()
            with (
                lease("copy"),
                transport.session(
                    root, enabled=os.getenv("RAW_ARCHIVE_REUSE_SSH", "false") == "true"
                ),
            ):
                yield ledger, Store(transport), checkpoint
        finally:
            health.close()
            ledger.close()


def engine(source, *, writing=False):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    from app.raw_archive.runner import metadata_source_settings

    value = (
        os.environ.get("TIERED_ARCHIVE_" + source.upper() + "_WRITER_DATABASE_URL")
        if writing
        else metadata_source_settings(source, environment())["database"]
    )
    if not value:
        raise ArchiveBlocked("Explicit source-local writer profile required")
    if writing:
        from sqlalchemy.engine import make_url

        reader = make_url(metadata_source_settings(source, environment())["database"])
        writer = make_url(value)
        if (reader.host, reader.port, reader.database) != (
            writer.host,
            writer.port,
            writer.database,
        ):
            raise ArchiveBlocked("Deletion writer is bound to another database")
    return create_engine(
        value,
        # Bounded jobs create several short-lived adapters. Do not leave idle
        # connections behind after each closed session/scoped export.
        poolclass=NullPool,
        connect_args={
            "options": (
                "-c statement_timeout=8000 -c lock_timeout=150 -c work_mem=4096"
                + (" -c idle_in_transaction_session_timeout=0" if writing else "")
            )
        },
    )


def wav_source(source, checkpoint, *, writing=False):
    import boto3
    from botocore.config import Config as BotoConfig
    from sqlalchemy.orm import sessionmaker

    from app.raw_archive.catalog import DirectCatalog, GatewayCatalog
    from app.raw_archive.runner import source_settings
    from app.raw_archive.storage import S3Source
    from app.raw_archive.worker import configuration

    from .wav_source import WavSource

    checkpoint()  # The planning caller also holds the actual source authority.
    settings = source_settings(source, environment())
    sessions = sessionmaker(engine(source, writing=writing), expire_on_commit=False)
    catalog = (
        GatewayCatalog(sessions, bucket=settings["bucket"])
        if source == "gateway"
        else DirectCatalog(sessions, settings["bucket"])
    )
    client = boto3.client(
        "s3",
        endpoint_url=settings["endpoint"],
        aws_access_key_id=settings["access"] if not writing else writer_s3_credentials(source)[0],
        aws_secret_access_key=settings["secret"]
        if not writing
        else writer_s3_credentials(source)[1],
        config=BotoConfig(connect_timeout=3, read_timeout=8, retries={"max_attempts": 1}),
    )
    return WavSource(
        sessions, catalog, S3Source(client), source, environment(), configuration(), checkpoint
    )


def writer_s3_credentials(source):
    prefix = "TIERED_ARCHIVE_" + source.upper() + "_WRITER_S3_"
    values = (os.environ.get(prefix + "ACCESS_KEY"), os.environ.get(prefix + "SECRET_KEY"))
    if not all(values):
        raise ArchiveBlocked("Separate source deletion S3 credentials required")
    return values


@lru_cache(maxsize=1)
def direct_wav_reader(namespace):
    import boto3
    from botocore.config import Config as BotoConfig

    from app.direct.payloads import WavReader
    from app.raw_archive.reader import get_object
    from app.raw_archive.runner import source_settings

    settings = source_settings("direct", namespace)
    client = boto3.client(
        "s3",
        endpoint_url=settings["endpoint"],
        aws_access_key_id=settings["access"],
        aws_secret_access_key=settings["secret"],
        config=BotoConfig(connect_timeout=3, read_timeout=8, retries={"max_attempts": 1}),
    )
    maximum = min(64 * 1024**2, int(os.environ.get("RAW_ARCHIVE_MAX_FILE_BYTES", str(8 * 1024**2))))

    def get(key):
        if (
            not isinstance(key, str)
            or len(key) > 1024
            or not key.endswith(".wav")
            or ".." in key.split("/")
        ):
            raise ArchiveBlocked("Invalid Direct reconstruction WAV key")
        response = get_object(client, Bucket=settings["bucket"], Key=key, kind="direct")
        try:
            body = response["Body"].read(maximum + 1)
        finally:
            response["Body"].close()
        if len(body) > maximum:
            raise ArchiveBlocked("Reconstruction WAV exceeds existing worker budget")
        return body

    return WavReader(get)


def direct_payload_reader(_db, row, context):
    from app.direct.models import Chunk, PayloadReference
    from app.direct.payloads import read_payload_snapshot

    chunk = Chunk(**row)
    reference = (
        PayloadReference(**context["payload_reference"]) if context["payload_reference"] else None
    )
    return read_payload_snapshot(
        chunk, reference, context["session_config"], direct_wav_reader(environment())
    )


def publish_sql(source, dataset, organization, start, end, *, limit=500, after=None):
    from . import parquet
    from .pipeline import publish
    from .source import Source

    with worker() as (ledger, store, checkpoint):
        adapter = Source(
            engine(source),
            source,
            environment(),
            checkpoint=checkpoint,
            payload_reader=direct_payload_reader if source == "direct" else None,
        )
        rows, flags = adapter.export(dataset, organization, start, end, limit=limit, after=after)
        if not rows:
            return {"status": "empty"}
        with tempfile.TemporaryDirectory(dir=ledger.root) as folder:
            path = Path(folder) / "rows.parquet"
            metadata = dict(parquet.write(path, rows, checkpoint=checkpoint), format="parquet")
            candidate = adapter.candidate(
                dataset, organization, start, end, metadata, flags, settled_at=end
            )
            receipt = publish(candidate, [(path, metadata)], store, ledger, checkpoint=checkpoint)
            return {
                "receipt": receipt,
                "next_cursor": adapter.next_cursor,
                "full_page": adapter.page_full,
            }


def publish_wav(source, reference, *, maximum_bytes=None, maximum_wavs=None):
    from . import parquet
    from .pipeline import publish

    with worker() as (ledger, store, checkpoint):
        adapter = wav_source(source, checkpoint)
        with tempfile.TemporaryDirectory(dir=ledger.root) as folder:
            folder = Path(folder)
            envelope, artifacts, flags = adapter.export(
                reference, folder, maximum_bytes=maximum_bytes, maximum_wavs=maximum_wavs
            )
            path = folder / "metadata.parquet"
            metadata = dict(
                parquet.write(path, [envelope], checkpoint=checkpoint), format="parquet"
            )
            candidate = adapter.candidate(envelope, metadata, flags)
            receipt = publish(
                candidate, [(path, metadata), *artifacts], store, ledger, checkpoint=checkpoint
            )
            adapter.publish_legacy_receipts(envelope, store.transport, datetime.now(UTC))
            return receipt


def stage_wav(source, reference, *, maximum_bytes, maximum_wavs, can_continue=lambda: True):
    from .wav_copy import advance

    with worker() as (ledger, store, checkpoint):
        adapter = wav_source(source, checkpoint)
        result = advance(
            adapter,
            reference,
            store,
            ledger,
            bindings=bindings_sha256(store),
            checkpoint=checkpoint,
            maximum_bytes=maximum_bytes,
            maximum_wavs=maximum_wavs,
            can_continue=can_continue,
        )
        envelope = result.pop("envelope", None)
        if result["complete"] and envelope is not None:
            adapter.publish_legacy_receipts(envelope, store.transport, datetime.now(UTC))
        return result


def verify(identity, snapshot_id):
    import httpx

    from app.raw_archive.snapshot_api import SnapshotAPI, private_token

    from .pipeline import verify_snapshot

    with worker() as (ledger, store, checkpoint):
        receipt = ledger.get(identity)
        if receipt is None:
            raise ArchiveBlocked("Unknown archive bundle")
        with httpx.Client(
            headers={
                "Authorization": "Bearer "
                + private_token(Path(os.environ["TIERED_ARCHIVE_SNAPSHOT_TOKEN_FILE"]))
            },
            timeout=httpx.Timeout(8, connect=3),
            trust_env=False,
        ) as client:
            provider = SnapshotAPI(
                client,
                int(os.environ["TIERED_ARCHIVE_BOX_ID"]),
                os.environ["RAW_ARCHIVE_SFTP_USER"],
                os.environ["RAW_ARCHIVE_SFTP_HOST"],
            )
            return verify_snapshot(
                candidate_from_json(receipt["source"]),
                store,
                ledger,
                provider,
                snapshot_id,
                checkpoint=checkpoint,
            )


def evict(identity):
    from . import parquet
    from .deletion import evict as delete_exact
    from .deletion import wav_only
    from .policy import utc

    if os.getenv("TIERED_ARCHIVE_DELETE_ENABLED", "false") != "true":
        raise ArchiveBlocked("Tiered source deletion disabled")
    with worker(deleting=True) as (ledger, store, checkpoint):
        receipt = ledger.get(identity)
        if not receipt or receipt.get("destination") != store.identity:
            raise ArchiveBlocked("Unknown deletion bundle")
        candidate = candidate_from_json(receipt["source"])
        wav_only(candidate)
        from datetime import timedelta

        from .backup import require_current

        def proof_guard(current_receipt, at):
            database_backup = require_current(
                ledger.scope(
                    environment(),
                    candidate.source,
                    "00000000-0000-0000-0000-000000000000",
                    "database_backup",
                    at - timedelta(hours=24),
                    at,
                ),
                source=candidate.source,
                environment=environment(),
                destination=store.identity,
                now=at,
            )
            import httpx

            from app.raw_archive.snapshot_api import SnapshotAPI, private_token

            snapshots = [current_receipt["snapshot"], database_backup["snapshot"]]
            with httpx.Client(
                headers={
                    "Authorization": "Bearer "
                    + private_token(Path(os.environ["TIERED_ARCHIVE_SNAPSHOT_TOKEN_FILE"]))
                },
                timeout=httpx.Timeout(8, connect=3),
                trust_env=False,
            ) as client:
                provider = SnapshotAPI(
                    client,
                    int(os.environ["TIERED_ARCHIVE_BOX_ID"]),
                    os.environ["RAW_ARCHIVE_SFTP_USER"],
                    os.environ["RAW_ARCHIVE_SFTP_HOST"],
                )
                for snapshot in snapshots:
                    live = provider.read(snapshot["provider_id"])
                    if (
                        live.get("id") != snapshot["provider_id"]
                        or live.get("storage_box") != snapshot["box_id"]
                        or live.get("name") != snapshot["name"]
                        or utc(live.get("created")) != utc(snapshot["created_at"])
                    ):
                        raise ArchiveBlocked(
                            "Required WAV or database snapshot was removed or changed"
                        )
            require_current(
                [database_backup],
                source=candidate.source,
                environment=environment(),
                destination=store.identity,
                now=datetime.now(UTC),
            )

        adapter = wav_source(candidate.source, checkpoint, writing=True)
        with tempfile.TemporaryDirectory(dir=ledger.root) as folder:
            path = Path(folder) / "source.parquet"
            reference = receipt["files"][0]
            store.download(reference["key"], path, reference["bytes"])
            adapter.expected = list(parquet.read(path, reference, checkpoint=checkpoint))
            return delete_exact(
                candidate,
                adapter,
                ledger,
                authorization_path=os.environ["TIERED_ARCHIVE_ACTIVATION_FILE"],
                authorization_sha256=os.environ["TIERED_ARCHIVE_ACTIVATION_SHA256"],
                runtime_sha256=runtime_sha256(),
                bindings_sha256=bindings_sha256(store),
                checkpoint=checkpoint,
                clock=lambda: datetime.now(UTC),
                proof_guard=proof_guard,
            )


def latest_snapshot():
    import httpx

    from app.raw_archive.snapshot_api import SnapshotAPI, private_token

    with httpx.Client(
        headers={
            "Authorization": "Bearer "
            + private_token(Path(os.environ["TIERED_ARCHIVE_SNAPSHOT_TOKEN_FILE"]))
        },
        timeout=httpx.Timeout(8, connect=3),
        trust_env=False,
    ) as client:
        provider = SnapshotAPI(
            client,
            int(os.environ["TIERED_ARCHIVE_BOX_ID"]),
            os.environ["RAW_ARCHIVE_SFTP_USER"],
            os.environ["RAW_ARCHIVE_SFTP_HOST"],
        )
        provider.validate_box()
        result = provider.request("GET", f"/storage_boxes/{provider.box_id}/snapshots?per_page=100")
        snapshots = result.get("snapshots", [])
        if not snapshots or len(snapshots) > 100:
            raise ArchiveBlocked("Scheduled Storage Box snapshot unavailable")
        return max(snapshots, key=lambda row: row["created"])["id"]


def backup(source, *, backup_format="schema-master-v2"):
    from . import parquet
    from .backup import decrypt, dump, encrypt, key_from_file
    from .codec import digest
    from .pipeline import publish
    from .policy import Candidate

    if backup_format not in {"schema-master-v2", "postgres-full-v1"}:
        raise ArchiveBlocked("Unknown database backup format")
    if backup_format == "postgres-full-v1" and environment() != "staging":
        raise ArchiveBlocked("Native full backup is currently restricted to Staging")

    with worker() as (ledger, store, checkpoint):
        from sqlalchemy.engine import make_url

        from app.raw_archive.runner import metadata_source_settings

        database_url = os.environ["TIERED_ARCHIVE_" + source.upper() + "_BACKUP_DATABASE_URL"]
        expected = make_url(metadata_source_settings(source, environment())["database"])
        selected = make_url(database_url)
        if (expected.host, expected.port or 5432, expected.database) != (
            selected.host,
            selected.port or 5432,
            selected.database,
        ):
            raise ArchiveBlocked("Backup profile belongs to another source database")
        key = key_from_file(Path(os.environ["TIERED_ARCHIVE_BACKUP_KEY_FILE"]))
        with tempfile.TemporaryDirectory(dir=ledger.root) as folder:
            folder = Path(folder)
            if backup_format == "postgres-full-v1":
                from app.raw_archive.policy import checksum

                from .native_backup import capture

                if selected != expected:
                    raise ArchiveBlocked("Native backup must use the exact existing source binding")
                if checksum(Path(os.environ["TIERED_ARCHIVE_BACKUP_KEY_FILE"])) != os.environ.get(
                    "TIERED_ARCHIVE_BACKUP_KEY_SHA256"
                ):
                    raise ArchiveBlocked("Native backup key differs from the recovery binding")
                candidate, files = capture(
                    database_url,
                    source,
                    folder,
                    key,
                    environment=environment(),
                    checkpoint=checkpoint,
                )
                return publish(candidate, files, store, ledger, checkpoint=checkpoint)
            originals, proof = dump(
                database_url,
                source,
                folder,
                checkpoint=checkpoint,
                with_proof=True,
            )
            files = []
            for path in originals:
                sealed = path.with_suffix(".encrypted")
                metadata = encrypt(path, sealed, key, checkpoint=checkpoint)
                restored = path.with_suffix(".verify")
                decrypt(sealed, restored, key, metadata["plaintext_sha256"], checkpoint=checkpoint)
                restored.unlink()
                path.unlink()
                files.append((sealed, metadata))
            at = datetime.now(UTC)
            body = {
                "source": source,
                "at": at,
                "files": [meta for _, meta in files],
                "master_proof": proof,
            }
            # The backup catalogue has an explicit system namespace and is never
            # returned through tenant-facing historical query routes.
            system_org = "00000000-0000-0000-0000-000000000000"
            row = {
                "identity": digest(body),
                "organization": system_org,
                "sensor": "system",
                "time_us": int(at.timestamp() * 1_000_000),
                "body": body,
            }
            path = folder / "backup-index.parquet"
            metadata = dict(
                parquet.write(path, [row], checkpoint=checkpoint),
                format="parquet",
                master_proof_version=proof["version"],
            )
            from datetime import timedelta

            candidate = Candidate(
                environment(),
                source,
                "database_backup",
                system_org,
                at - timedelta(microseconds=1),
                at,
                metadata["content_sha256"],
                1,
                "features",
                at,
                active=True,
            )
            return publish(
                candidate, [(path, metadata), *files], store, ledger, checkpoint=checkpoint
            )
