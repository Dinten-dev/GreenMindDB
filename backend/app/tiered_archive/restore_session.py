"""Bounded, atomic SQL restore batches into one operator-isolated local database.

The source system is never contacted. No application writers may use this
_restore_ database. An independently pinned immutable plan head commits to a
hash-linked sequence of bounded batch descriptors, not an unbounded input list.
The existing single-bundle restore contract remains unchanged.
"""

import hashlib
import json
import re
from pathlib import Path

from app.raw_archive.policy import ArchiveBlocked

from . import parquet
from .codec import digest, encode
from .recovery import direct_children, restore_url
from .runtime import candidate_from_json
from .source import DATASETS, row_identity

MAX_PAGES = 32
MAX_PARTS = 128
FORMAT = "isolated-sql-restore-plan-v1"
MASTERS = {
    "ingest_log": ("gateway", "zone"),
    "gateway_state_report": ("gateway", "zone"),
    "sensor_reading": ("sensor", "gateway", "zone"),
    "visual_reading": ("sensor", "gateway", "zone"),
    "visual_wave": ("sensor", "gateway", "zone", "wav_file", "visual_wav"),
    "wav_feature": ("wav_file", "sensor", "gateway", "zone"),
    "wav_feature_version": ("wav_file", "sensor", "gateway", "zone"),
    "direct_visual_point": ("direct_device", "direct_segment", "direct_visual_segment"),
    "direct_revision": ("direct_segment", "direct_device", "direct_session"),
    "direct_chunk": ("direct_segment", "direct_device", "direct_session"),
}


def require(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


def sha(value):
    return isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value) is not None


def plan_scope(plan):
    return {key: value for key, value in plan.items() if key != "first_batch_sha256"}


def validate_plan(plan, expected_sha256):
    import uuid

    require(
        isinstance(plan, dict)
        and set(plan)
        == {
            "format",
            "environment",
            "source",
            "dataset",
            "organization",
            "schema_master_sha256",
            "expected_pages",
            "expected_rows",
            "first_batch_sha256",
        },
        "Exact restore plan head required",
    )
    require(sha(expected_sha256) and digest(plan) == expected_sha256, "Restore plan head changed")
    require(
        plan["format"] == FORMAT and plan["environment"] in {"production", "staging"},
        "Explicit versioned restore environment required",
    )
    require(
        plan["dataset"] in DATASETS and DATASETS[plan["dataset"]].source == plan["source"],
        "Restore plan dataset/source mismatch",
    )
    uuid.UUID(plan["organization"])
    require(
        sha(plan["schema_master_sha256"]) and sha(plan["first_batch_sha256"]),
        "Independent schema/master and chain pins required",
    )
    require(
        all(
            type(plan[key]) is int and 0 < plan[key] < 2**63
            for key in ("expected_pages", "expected_rows")
        ),
        "Positive restore totals required",
    )


def private_json(path, expected):
    require(
        path.is_absolute()
        and not path.is_symlink()
        and path.is_file()
        and 0 < path.stat().st_size <= 1024**2,
        "Bounded regular restore JSON required",
    )
    raw = path.read_bytes()
    require(
        len(raw) <= 1024**2 and hashlib.sha256(raw).hexdigest() == expected,
        "Restore JSON checksum mismatch",
    )
    return json.loads(raw)


def target_identity(db):
    from sqlalchemy import text

    value = (
        db.execute(
            text("""SELECT current_database() AS name,
        (SELECT oid::bigint FROM pg_catalog.pg_database WHERE datname=current_database()) AS oid,
        inet_server_addr()::text AS address, inet_server_port() AS port""")
        )
        .mappings()
        .one()
    )
    return digest(dict(value))


def journals(db):
    from sqlalchemy import JSON, BigInteger, Column, MetaData, String, Table

    metadata = MetaData()
    sessions = Table(
        "greenmind_restore_session",
        metadata,
        Column("id", String(64), primary_key=True),
        Column("plan_sha256", String(64), nullable=False),
        Column("dataset", String(64), nullable=False),
        Column("scope_sha256", String(64), nullable=False),
        Column("target_sha256", String(64), nullable=False),
        Column("masters_sha256", String(64), nullable=False),
        Column("schema_sha256", String(64), nullable=False),
        Column("next_sha256", String(64)),
        Column("batches", BigInteger, nullable=False),
        Column("pages", BigInteger, nullable=False),
        Column("rows", BigInteger, nullable=False),
        Column("child_counts", JSON, nullable=False),
        Column("state", String(16), nullable=False),
    )
    batches = Table(
        "greenmind_restore_batch",
        metadata,
        Column("session_id", String(64), primary_key=True),
        Column("sha256", String(64), primary_key=True),
        Column("sequence", BigInteger, nullable=False),
        Column("pages", BigInteger, nullable=False),
        Column("rows", BigInteger, nullable=False),
        Column("proof_sha256", String(64), nullable=False),
    )
    pages = Table(
        "greenmind_restore_page",
        metadata,
        Column("session_id", String(64), primary_key=True),
        Column("candidate_id", String(64), primary_key=True),
        Column("batch_sha256", String(64), nullable=False),
        Column("manifest_sha256", String(64), nullable=False),
        Column("rows", BigInteger, nullable=False),
        Column("proof_sha256", String(64), nullable=False),
    )
    # Only the dedicated restore target receives these journal tables. Source
    # schemas and application tables are never altered by this module.
    metadata.create_all(db)
    return sessions, batches, pages


def lock_and_tables(db, plan, checkpoint):
    from sqlalchemy import MetaData, Table, text

    metadata = MetaData()
    spec = DATASETS[plan["dataset"]]
    target = Table(spec.table, metadata, autoload_with=db)
    children = {
        name: Table(name, metadata, autoload_with=db)
        for name in (
            ("direct_chunk_payload_ref", "direct_piece") if spec.table == "direct_chunk" else ()
        )
    }
    masters = {name: Table(name, metadata, autoload_with=db) for name in MASTERS[plan["dataset"]]}
    require(
        set(target.primary_key.columns.keys()) == set(spec.keys),
        "Restore primary key differs from source contract",
    )
    for table in [target, *children.values(), *masters.values()]:
        checkpoint()
        db.execute(text(f'LOCK TABLE "{table.name}" IN SHARE ROW EXCLUSIVE MODE'))
    return spec, target, children, masters


def schema_fingerprint(tables):
    return digest(
        {
            table.name: {
                "columns": [
                    (
                        column.name,
                        str(column.type),
                        column.nullable,
                        column.primary_key,
                        str(column.server_default.arg)
                        if column.server_default is not None
                        else None,
                    )
                    for column in table.columns
                ],
                "foreign_keys": sorted(
                    (foreign.parent.name, foreign.target_fullname, foreign.ondelete or "")
                    for foreign in table.foreign_keys
                ),
                "unique": sorted(
                    tuple(column.name for column in constraint.columns)
                    for constraint in table.constraints
                    if type(constraint).__name__ == "UniqueConstraint"
                ),
            }
            for table in sorted(tables, key=lambda item: item.name)
        }
    )


def master_fingerprint(masters, db, checkpoint):
    from sqlalchemy import select

    result = hashlib.sha256()
    for name, table in sorted(masters.items()):
        require(bool(list(table.primary_key)), "Restored master requires deterministic primary key")
        header = encode(
            {
                "table": name,
                "columns": [
                    (column.name, str(column.type), column.nullable) for column in table.columns
                ],
            }
        )
        result.update(len(header).to_bytes(8, "big") + header)
        query = select(table).order_by(*table.primary_key.columns).execution_options(yield_per=16)
        with db.execute(query) as rows:
            for row in rows.mappings():
                checkpoint()
                raw = encode(dict(row))
                result.update(len(raw).to_bytes(8, "big") + raw)
    # Only the digest persists: no private master values enter journal/logs.
    return result.hexdigest()


def counts(db, target, children):
    from sqlalchemy import func, select

    return {
        table.name: db.execute(select(func.count()).select_from(table)).scalar_one()
        for table in [target, *children.values()]
    }


def connection(database_url):
    from sqlalchemy import create_engine

    return create_engine(restore_url(database_url), pool_size=1, max_overflow=0)


def admission(db):
    from sqlalchemy import Boolean, text

    db.execute(text("SET LOCAL lock_timeout='150ms'"))
    db.execute(text("SET LOCAL statement_timeout='8000ms'"))
    db.execute(text("SET LOCAL idle_in_transaction_session_timeout='0'"))
    require(
        db.execute(
            text("SELECT pg_try_advisory_xact_lock(1246973778, 1381192786) AS acquired").columns(
                acquired=Boolean
            )
        ).scalar_one()
        is True,
        "Another restore batch owns the isolated target",
    )


def begin_restore_session(database_url, plan, plan_sha256, *, checkpoint):
    """Begin once on empty targets; subsequent calls use returned session identity."""
    from sqlalchemy import select

    validate_plan(plan, plan_sha256)
    engine = connection(database_url)
    try:
        with engine.begin() as db:
            admission(db)
            sessions, _, _ = journals(db)
            identity = target_identity(db)
            session_id = digest({"plan": plan_sha256, "target": identity})
            existing = (
                db.execute(select(sessions).where(sessions.c.dataset == plan["dataset"]))
                .mappings()
                .one_or_none()
            )
            _, target, children, masters = lock_and_tables(db, plan, checkpoint)
            schema_sha = schema_fingerprint([target, *children.values(), *masters.values()])
            masters_sha = master_fingerprint(masters, db, checkpoint)
            actual_counts = counts(db, target, children)
            resumed = existing is not None
            if resumed:
                # Crash after BEGIN's commit but before its response does not
                # require a new session, a reset, or any unjournaled adoption.
                require(
                    existing["id"] == session_id
                    and existing["plan_sha256"] == plan_sha256
                    and existing["target_sha256"] == identity
                    and existing["scope_sha256"] == digest(plan_scope(plan)),
                    "Restore target already belongs to a different session",
                )
                require(
                    existing["schema_sha256"] == schema_sha
                    and existing["masters_sha256"] == masters_sha,
                    "Owned restore session schema or masters changed",
                )
                require(
                    actual_counts == {target.name: existing["rows"], **existing["child_counts"]},
                    "Unjournaled target rows detected",
                )
            else:
                require(
                    not any(actual_counts.values()), "Restore begins only with empty target tables"
                )
                checkpoint()
                db.execute(
                    sessions.insert().values(
                        id=session_id,
                        plan_sha256=plan_sha256,
                        dataset=plan["dataset"],
                        scope_sha256=digest(plan_scope(plan)),
                        target_sha256=identity,
                        masters_sha256=masters_sha,
                        schema_sha256=schema_sha,
                        next_sha256=plan["first_batch_sha256"],
                        batches=0,
                        pages=0,
                        rows=0,
                        child_counts=dict.fromkeys(children, 0),
                        state="open",
                    )
                )

        return {
            "session_id": session_id,
            "plan_sha256": plan_sha256,
            "source_accesses": 0,
            "resumed_session": resumed,
        }
    finally:
        engine.dispose()


def load_batch(path, expected_sha256, plan, checkpoint):
    data = private_json(path, expected_sha256)
    require(
        set(data) == {"version", "scope_sha256", "sequence", "next_sha256", "pages"}
        and type(data["version"]) is int
        and data["version"] == 1
        and data["scope_sha256"] == digest(plan_scope(plan))
        and type(data["sequence"]) is int
        and data["sequence"] >= 0
        and (data["next_sha256"] is None or sha(data["next_sha256"])),
        "Restore batch scope mismatch",
    )
    require(
        isinstance(data["pages"], list) and 1 <= len(data["pages"]) <= MAX_PAGES,
        "Bounded nonempty restore batch required",
    )
    pages = []
    ids = set()
    parts = rows = decoded = size = 0
    for pin in data["pages"]:
        checkpoint()
        require(
            isinstance(pin, dict)
            and set(pin) == {"folder", "manifest_sha256", "candidate_id"}
            and sha(pin["manifest_sha256"])
            and sha(pin["candidate_id"]),
            "Exact SQL page pins required",
        )
        folder = Path(pin["folder"])
        require(
            folder.is_absolute() and folder.is_dir() and not folder.is_symlink(),
            "Isolated SQL page folder required",
        )
        manifest = private_json(folder / "manifest.json", pin["manifest_sha256"])
        candidate = candidate_from_json(manifest["candidate"])
        candidate.validate()
        require(
            candidate.identity == pin["candidate_id"] == manifest["identity"]
            and all(
                getattr(candidate, key) == plan[key]
                for key in ("environment", "source", "dataset", "organization")
            )
            and candidate.kind == DATASETS[plan["dataset"]].kind
            and type(candidate.rows) is int,
            "SQL page source or tenant mismatch",
        )
        require(candidate.identity not in ids, "Duplicate SQL candidate in batch")
        ids.add(candidate.identity)
        references = manifest["files"]
        require(
            isinstance(references, list)
            and 1 <= len(references) <= MAX_PARTS
            and references[0]["content_sha256"] == candidate.content_sha256
            and all(
                part["format"] == "parquet"
                and type(part["rows"]) is int
                and part["rows"] > 0
                and type(part["bytes"]) is int
                and part["bytes"] > 0
                and type(part["decoded_bytes"]) is int
                and part["decoded_bytes"] > 0
                for part in references
            )
            and sum(part["rows"] for part in references) == candidate.rows,
            "Complete bounded SQL page parts required",
        )
        parts += len(references)
        rows += candidate.rows
        size += sum(part["bytes"] for part in references)
        decoded += sum(part["decoded_bytes"] for part in references)
        require(
            parts <= MAX_PARTS
            and rows <= parquet.MAX_ROWS
            and size <= parquet.MAX_FILE_BYTES
            and decoded <= parquet.MAX_DECODED_BYTES,
            "Restore batch exceeds row/file/decoded budget",
        )
        pages.append((pin, folder, manifest, candidate))
    return data, pages


def page_rows(page, spec, checkpoint):
    _, folder, manifest, candidate = page
    count = 0
    for number, reference in enumerate(manifest["files"]):
        path = folder / f"{number:04d}.parquet"
        require(not path.is_symlink(), "Symlink is not an isolated restore part")
        for envelope in parquet.read(path, reference, checkpoint=checkpoint):
            body = envelope["body"]
            require(
                envelope["organization"] == candidate.organization
                and set(body) == {"row", "context"}
                and envelope["identity"] == row_identity(spec, body["row"]),
                "SQL row identity or tenant mismatch",
            )
            checkpoint()
            count += 1
            yield body
    require(count == candidate.rows, "SQL page row count mismatch")


def check_row(db, target, children, spec, body):
    from sqlalchemy import and_, select

    expected = body["row"]
    actual = db.execute(
        select(target).where(and_(*(target.c[key] == expected[key] for key in spec.keys)))
    )
    actual = actual.mappings().one_or_none()
    require(
        actual is not None and encode(dict(actual)) == encode(expected),
        "Stored SQL values differ from pinned page",
    )
    normalized = {"row": dict(actual), "children": {}}
    for name, expected_rows in direct_children(body).items() if children else ():
        stored = db.execute(
            select(children[name]).where(children[name].c.chunk_id == expected["id"])
        ).mappings()
        values = sorted(encode(dict(row)) for row in stored)
        require(
            values == sorted(encode(row) for row in expected_rows),
            "Stored Direct children differ from pinned page",
        )
        normalized["children"][name] = values
    return encode(normalized)


def apply_restore_batch(
    database_url, plan, plan_sha256, session_id, batch_path, batch_sha256, *, checkpoint
):
    """Commit at most one bounded batch; failed batches roll back rows and journal."""
    from sqlalchemy import select, text

    validate_plan(plan, plan_sha256)
    require(sha(session_id) and sha(batch_sha256), "Pinned session and batch required")
    descriptor, pages = load_batch(batch_path, batch_sha256, plan, checkpoint)
    engine = connection(database_url)
    try:
        with engine.begin() as db:
            admission(db)
            sessions, batches, journal = journals(db)
            session = (
                db.execute(select(sessions).where(sessions.c.id == session_id).with_for_update())
                .mappings()
                .one_or_none()
            )
            require(
                session is not None
                and session["plan_sha256"] == plan_sha256
                and session["scope_sha256"] == descriptor["scope_sha256"]
                and session["target_sha256"] == target_identity(db),
                "Restore session/target binding mismatch",
            )
            spec, target, children, masters = lock_and_tables(db, plan, checkpoint)
            require(
                schema_fingerprint([target, *children.values(), *masters.values()])
                == session["schema_sha256"],
                "Restored target schema changed",
            )
            require(
                master_fingerprint(masters, db, checkpoint) == session["masters_sha256"],
                "Restored masters changed",
            )
            current = counts(db, target, children)
            require(
                current == {target.name: session["rows"], **session["child_counts"]},
                "Unjournaled target rows detected",
            )
            prior = (
                db.execute(
                    select(batches).where(
                        batches.c.session_id == session_id, batches.c.sha256 == batch_sha256
                    )
                )
                .mappings()
                .one_or_none()
            )
            replay = prior is not None
            require(
                (replay and prior["sequence"] == descriptor["sequence"])
                or (
                    not replay
                    and session["state"] == "open"
                    and session["next_sha256"] == batch_sha256
                    and session["batches"] == descriptor["sequence"]
                ),
                "Restore chain sequence or next link mismatch",
            )
            batch_proof = hashlib.sha256()
            added = 0
            child_counts = dict(session["child_counts"])
            for page in pages:
                pin, _, _, candidate = page
                previous = (
                    db.execute(
                        select(journal).where(
                            journal.c.session_id == session_id,
                            journal.c.candidate_id == candidate.identity,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                require(
                    (
                        replay
                        and previous is not None
                        and previous["batch_sha256"] == batch_sha256
                        and previous["manifest_sha256"] == pin["manifest_sha256"]
                    )
                    or (not replay and previous is None),
                    "SQL page already belongs to another batch or proof",
                )
                proof = hashlib.sha256()
                for body in page_rows(page, spec, checkpoint):
                    if not replay:
                        # Plain INSERT only. Any duplicate or differing PK aborts
                        # the whole batch; no upsert or ON CONFLICT is permitted.
                        db.execute(target.insert().values(**body["row"]))
                        added += 1
                        for name, rows in direct_children(body).items() if children else ():
                            for row in rows:
                                db.execute(children[name].insert().values(**row))
                                child_counts[name] += 1
                    raw = check_row(db, target, children, spec, body)
                    proof.update(len(raw).to_bytes(8, "big") + raw)
                # Read every input again and compare persisted typed values before
                # accepting the page. This also sees effects of later row triggers.
                second = hashlib.sha256()
                for body in page_rows(page, spec, checkpoint):
                    raw = check_row(db, target, children, spec, body)
                    second.update(len(raw).to_bytes(8, "big") + raw)
                require(proof.digest() == second.digest(), "SQL page changed during restoration")
                page_proof = proof.hexdigest()
                if replay:
                    require(
                        previous["proof_sha256"] == page_proof
                        and previous["rows"] == candidate.rows,
                        "Replayed SQL page proof differs",
                    )
                else:
                    db.execute(
                        journal.insert().values(
                            session_id=session_id,
                            candidate_id=candidate.identity,
                            batch_sha256=batch_sha256,
                            manifest_sha256=pin["manifest_sha256"],
                            rows=candidate.rows,
                            proof_sha256=page_proof,
                        )
                    )
                batch_proof.update(
                    encode(
                        {
                            "candidate": candidate.identity,
                            "manifest": pin["manifest_sha256"],
                            "proof": page_proof,
                            "rows": candidate.rows,
                        }
                    )
                )
            # A later insert/trigger may have altered a page checked earlier in
            # this batch. Compare every page again after the final insert.
            for page in pages:
                final_proof = hashlib.sha256()
                for body in page_rows(page, spec, checkpoint):
                    raw = check_row(db, target, children, spec, body)
                    final_proof.update(len(raw).to_bytes(8, "big") + raw)
                expected_proof = db.execute(
                    select(journal.c.proof_sha256).where(
                        journal.c.session_id == session_id,
                        journal.c.candidate_id == page[3].identity,
                    )
                ).scalar_one()
                require(
                    final_proof.hexdigest() == expected_proof,
                    "SQL page changed after later insertion",
                )
            if replay:
                require(
                    prior["proof_sha256"] == batch_proof.hexdigest(), "Replayed batch proof differs"
                )
            else:
                total_pages, total_rows = session["pages"] + len(pages), session["rows"] + added
                terminal = descriptor["next_sha256"] is None
                require(
                    total_pages <= plan["expected_pages"]
                    and total_rows <= plan["expected_rows"]
                    and (
                        not terminal
                        or (
                            total_pages == plan["expected_pages"]
                            and total_rows == plan["expected_rows"]
                        )
                    )
                    and (
                        terminal
                        or (
                            total_pages < plan["expected_pages"]
                            and total_rows < plan["expected_rows"]
                        )
                    ),
                    "Restore chain truncated or totals exceeded",
                )
                actual = counts(db, target, children)
                require(
                    actual == {target.name: total_rows, **child_counts},
                    "Stored restore counts differ from journal",
                )
                # The source's fixed allowlisted joins validate restored tenant
                # routing too; a matching envelope cannot launder foreign masters.
                routed = db.execute(
                    text(
                        f"SELECT count(*) FROM {spec.table} t {spec.joins} "
                        f"WHERE CAST({spec.organization} AS text)=:org"
                    ),
                    {"org": plan["organization"]},
                ).scalar_one()
                require(routed == total_rows, "Restored target tenant links mismatch")
                db.execute(
                    batches.insert().values(
                        session_id=session_id,
                        sha256=batch_sha256,
                        sequence=descriptor["sequence"],
                        pages=len(pages),
                        rows=added,
                        proof_sha256=batch_proof.hexdigest(),
                    )
                )
                db.execute(
                    sessions.update()
                    .where(sessions.c.id == session_id)
                    .values(
                        next_sha256=descriptor["next_sha256"],
                        batches=session["batches"] + 1,
                        pages=total_pages,
                        rows=total_rows,
                        child_counts=child_counts,
                        state="loaded" if terminal else "open",
                    )
                )
            # Bound descriptors/manifests again, including on idempotent replay.
            fresh, fresh_pages = load_batch(batch_path, batch_sha256, plan, checkpoint)
            require(
                fresh == descriptor and [p[2] for p in fresh_pages] == [p[2] for p in pages],
                "Restore input pins changed before commit",
            )
            require(
                master_fingerprint(masters, db, checkpoint) == session["masters_sha256"],
                "Restored masters changed before commit",
            )
            checkpoint()
            result = (
                db.execute(select(sessions).where(sessions.c.id == session_id)).mappings().one()
            )
        return {
            "session_id": session_id,
            "batch_sha256": batch_sha256,
            "replayed": replay,
            "committed_pages": result["pages"],
            "committed_rows": result["rows"],
            "chain_loaded": result["state"] == "loaded",
            "phase": "loaded" if result["state"] == "loaded" else "loading",
            "final_audit_required": True,
            "source_accesses": 0,
            "verification": "this_batch_full_typed_readback",
            "full_target_content_audit_complete": False,
            "production_ready": False,
        }
    finally:
        engine.dispose()
