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
FORMAT = "isolated-sql-restore-plan-v2"
MODE_FORMAT = "isolated-sql-restore-plan-v3"
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

    base_fields = {
        "format",
        "universe",
        "environment",
        "source",
        "dataset",
        "organization",
        "schema_master_sha256",
        "expected_pages",
        "expected_rows",
        "first_batch_sha256",
    }
    require(
        isinstance(plan, dict)
        and (
            (plan.get("format") == FORMAT and set(plan) == base_fields)
            or (
                plan.get("format") == MODE_FORMAT
                and set(plan) == base_fields | {"fence_mode", "fence_capability_sha256"}
            )
        ),
        "Exact versioned restore plan head required",
    )
    if plan["format"] == MODE_FORMAT:
        require(
            plan["fence_mode"] == "timescale-origin-guarded-v1"
            and sha(plan["fence_capability_sha256"]),
            "Explicit supported origin mode and independent capability pin required",
        )
    require(sha(expected_sha256) and digest(plan) == expected_sha256, "Restore plan head changed")
    require(
        plan["format"] in {FORMAT, MODE_FORMAT}
        and plan["environment"] in {"production", "staging"},
        "Explicit versioned restore environment required",
    )
    require(
        plan["dataset"] in DATASETS and DATASETS[plan["dataset"]].source == plan["source"],
        "Restore plan dataset/source mismatch",
    )
    uuid.UUID(plan["organization"])
    from .restore_universe import validate

    validate(plan)
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
    with path.open("rb") as source:
        raw = source.read(1024**2 + 1)
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
        Column("organization", String(36), nullable=False),
        Column("audit_next_sha256", String(64)),
        Column("audit_epoch", BigInteger),
        Column("audit_batches", BigInteger, nullable=False),
        Column("audit_pages", BigInteger, nullable=False),
        Column("audit_rows", BigInteger, nullable=False),
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
    # schemas are never altered. The separate fence adds only owned triggers
    # to the NEW isolated restore target, never to the original source.
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
    from . import restore_universe as universe

    universe.bind(db, plan)
    all_tables, masters = universe.tables(db, plan)
    require(
        all_tables[target.name].name == target.name
        and all(name in all_tables for name in children),
        "Dataset or dependencies absent from complete restore universe",
    )
    require(
        set(target.primary_key.columns.keys()) == set(spec.keys),
        "Restore primary key differs from source contract",
    )
    for table in all_tables.values():
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


def connection(database_url):
    from sqlalchemy import create_engine

    return create_engine(
        restore_url(database_url),
        pool_size=1,
        max_overflow=0,
        connect_args={"connect_timeout": 3, "options": "-c default_transaction_read_only=off"},
    )


def admission(db):
    from sqlalchemy import Boolean, text

    db.execute(text("SET LOCAL search_path=public,pg_catalog"))
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


def dataset_total(db, dataset):
    from sqlalchemy import JSON, BigInteger, Column, MetaData, String, Table, select

    table = Table(
        "greenmind_restore_total",
        MetaData(),
        Column("dataset", String(64), primary_key=True),
        Column("rows", BigInteger, nullable=False),
        Column("children", JSON, nullable=False),
    )
    table.create(db, checkfirst=True)
    return table, db.execute(
        select(table).where(table.c.dataset == dataset)
    ).mappings().one_or_none()


def begin_restore_session(database_url, plan, plan_sha256, *, checkpoint):
    from sqlalchemy import select

    from . import restore_baseline as baseline
    from . import restore_fence as fence

    validate_plan(plan, plan_sha256)
    engine = connection(database_url)
    try:
        with engine.begin() as db:
            admission(db)
            sessions, _, _ = journals(db)
            identity = target_identity(db)
            session_id = digest({"plan": plan_sha256, "target": identity})
            existing = (
                db.execute(
                    select(sessions).where(
                        sessions.c.dataset == plan["dataset"],
                        sessions.c.organization == plan["organization"],
                    )
                )
                .mappings()
                .one_or_none()
            )
            _, target, children, masters = lock_and_tables(db, plan, checkpoint)
            from .restore_universe import domain
            from .restore_universe import tables as universe_tables

            protected, _ = universe_tables(db, plan)
            snapshot = fence.ensure(
                db,
                protected,
                set(masters),
                identity,
                domain(plan),
            )
            baseline.ensure(db, masters, snapshot, schema_fingerprint)
            anchor = baseline.anchor(db, masters, snapshot)
            schema_sha = schema_fingerprint([target, *children.values(), *masters.values()])
            totals, total = dataset_total(db, plan["dataset"])
            if total is None:
                require(
                    all(
                        snapshot["rows"][table.name] == 0 for table in [target, *children.values()]
                    ),
                    "Restore begins only with empty unowned tables",
                )
                db.execute(
                    totals.insert().values(
                        dataset=plan["dataset"], rows=0, children=dict.fromkeys(children, 0)
                    )
                )
                total = {"rows": 0, "children": dict.fromkeys(children, 0)}
            require(
                {table.name: snapshot["rows"][table.name] for table in [target, *children.values()]}
                == {target.name: total["rows"], **total["children"]},
                "Unjournaled shared target rows",
            )
            resumed = existing is not None
            if resumed:
                require(
                    existing["id"] == session_id
                    and existing["plan_sha256"] == plan_sha256
                    and existing["target_sha256"] == identity
                    and existing["scope_sha256"] == digest(plan_scope(plan)),
                    "Restore target already belongs to a different session",
                )
                require(
                    existing["schema_sha256"] == schema_sha
                    and existing["masters_sha256"] == anchor,
                    "Owned restore session schema or masters changed",
                )
            else:
                require(snapshot["phase"] == "loading", "No new sessions after audit freeze")
                db.execute(
                    sessions.insert().values(
                        id=session_id,
                        plan_sha256=plan_sha256,
                        dataset=plan["dataset"],
                        organization=plan["organization"],
                        scope_sha256=digest(plan_scope(plan)),
                        target_sha256=identity,
                        masters_sha256=anchor,
                        schema_sha256=schema_sha,
                        next_sha256=plan["first_batch_sha256"],
                        batches=0,
                        pages=0,
                        rows=0,
                        child_counts=dict.fromkeys(children, 0),
                        state="open",
                        audit_next_sha256=None,
                        audit_epoch=None,
                        audit_batches=0,
                        audit_pages=0,
                        audit_rows=0,
                    )
                )
            checkpoint()
        return {
            "session_id": session_id,
            "plan_sha256": plan_sha256,
            "source_accesses": 0,
            "resumed_session": resumed,
            "master_baseline_ready": anchor != "0" * 64,
        }
    finally:
        engine.dispose()


def advance_restore_baseline(
    database_url,
    plan,
    plan_sha256,
    session_id,
    *,
    checkpoint,
    maximum_rows=500,
    maximum_bytes=8 * 1024**2,
):
    from sqlalchemy import select

    from . import restore_baseline as baseline
    from . import restore_fence as fence

    validate_plan(plan, plan_sha256)
    engine = connection(database_url)
    try:
        with engine.begin() as db:
            admission(db)
            sessions, _, _ = journals(db)
            session = (
                db.execute(select(sessions).where(sessions.c.id == session_id).with_for_update())
                .mappings()
                .one()
            )
            require(
                session["plan_sha256"] == plan_sha256
                and session["target_sha256"] == target_identity(db),
                "Master baseline target binding mismatch",
            )
            snapshot = fence.check(db)
            require(snapshot["phase"] == "loading", "Master baseline must precede audit freeze")
            _, target, children, masters = lock_and_tables(db, plan, checkpoint)
            require(
                schema_fingerprint([target, *children.values(), *masters.values()])
                == session["schema_sha256"],
                "Restored target schema changed",
            )
            result = baseline.advance(
                db,
                masters,
                snapshot,
                checkpoint=checkpoint,
                maximum_rows=maximum_rows,
                maximum_bytes=maximum_bytes,
            )
            require(
                fence.check(db)["epoch"] == snapshot["epoch"], "Mutation during master baseline"
            )
            anchor = baseline.anchor(db, masters, snapshot)
            if anchor != "0" * 64:
                db.execute(sessions.update().values(masters_sha256=anchor))
            checkpoint()
        return dict(result, ready=anchor != "0" * 64, source_accesses=0)
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


def check_row(db, target, children, spec, body, organization):
    from sqlalchemy import and_, select, text

    expected = body["row"]
    actual = db.execute(
        select(target).where(and_(*(target.c[key] == expected[key] for key in spec.keys)))
    )
    actual = actual.mappings().one_or_none()
    require(
        actual is not None and encode(dict(actual)) == encode(expected),
        "Stored SQL values differ from pinned page",
    )
    predicate = " AND ".join(f't."{key}"=:key_{number}' for number, key in enumerate(spec.keys))
    route = db.execute(
        text(
            f"SELECT CAST({spec.organization} AS text) FROM {spec.table} t {spec.joins} WHERE {predicate}"
        ),
        {f"key_{number}": expected[key] for number, key in enumerate(spec.keys)},
    ).scalar_one_or_none()
    require(route == organization, "Restored target tenant links mismatch")
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
    from sqlalchemy import select

    from . import restore_baseline as baseline
    from . import restore_fence as fence

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
            before = fence.check(db)
            require(
                session["masters_sha256"] != "0" * 64
                and baseline.anchor(db, masters, before) == session["masters_sha256"],
                "Restored master baseline is incomplete or changed",
            )
            totals, total = dataset_total(db, plan["dataset"])
            require(
                total is not None
                and {
                    table.name: before["rows"][table.name] for table in [target, *children.values()]
                }
                == {target.name: total["rows"], **total["children"]},
                "Unjournaled shared target rows",
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
                    and before["phase"] == "loading"
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
                    raw = check_row(db, target, children, spec, body, plan["organization"])
                    proof.update(len(raw).to_bytes(8, "big") + raw)
                # Read every input again and compare persisted typed values before
                # accepting the page. This also sees effects of later row triggers.
                second = hashlib.sha256()
                for body in page_rows(page, spec, checkpoint):
                    raw = check_row(db, target, children, spec, body, plan["organization"])
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
                    raw = check_row(db, target, children, spec, body, plan["organization"])
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
                inserted = {
                    target.name: added,
                    **{
                        name: child_counts[name] - session["child_counts"][name]
                        for name in children
                    },
                }
                after = fence.accept(db, before, inserted)
                joint_children = {
                    name: total["children"][name] + inserted[name] for name in children
                }
                require(
                    {
                        table.name: after["rows"][table.name]
                        for table in [target, *children.values()]
                    }
                    == {target.name: total["rows"] + added, **joint_children},
                    "Stored joint restore counts differ",
                )
                db.execute(
                    totals.update()
                    .where(totals.c.dataset == plan["dataset"])
                    .values(rows=total["rows"] + added, children=joint_children)
                )
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
            latest = fence.check(db)
            require(
                baseline.anchor(db, masters, latest) == session["masters_sha256"],
                "Restored master baseline changed",
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
            "chain_loaded": result["state"] in {"loaded", "auditing", "verified"},
            "phase": result["state"] if result["state"] != "open" else "loading",
            "final_audit_required": True,
            "source_accesses": 0,
            "verification": "this_batch_full_typed_readback",
            "full_target_content_audit_complete": False,
            "production_ready": False,
        }
    finally:
        engine.dispose()


def audit_journal(db):
    from sqlalchemy import BigInteger, Column, MetaData, String, Table

    table = Table(
        "greenmind_restore_audit",
        MetaData(),
        Column("session_id", String(64), primary_key=True),
        Column("batch_sha256", String(64), primary_key=True),
        Column("sequence", BigInteger, nullable=False),
        Column("epoch", BigInteger, nullable=False),
        Column("pages", BigInteger, nullable=False),
        Column("rows", BigInteger, nullable=False),
        Column("proof_sha256", String(64), nullable=False),
    )
    table.create(db, checkfirst=True)
    return table


def begin_restore_audit(database_url, plan, plan_sha256, session_id, *, checkpoint):
    """Freeze all registered load sessions before any final content-audit call."""
    from sqlalchemy import select

    from . import restore_baseline as baseline
    from . import restore_fence as fence

    validate_plan(plan, plan_sha256)
    engine = connection(database_url)
    try:
        with engine.begin() as db:
            admission(db)
            sessions, _, _ = journals(db)
            session = (
                db.execute(select(sessions).where(sessions.c.id == session_id).with_for_update())
                .mappings()
                .one()
            )
            require(
                session["plan_sha256"] == plan_sha256
                and session["target_sha256"] == target_identity(db),
                "Audit session/target binding mismatch",
            )
            require(
                db.execute(
                    select(sessions.c.id)
                    .where(sessions.c.state.not_in(["loaded", "auditing", "verified"]))
                    .limit(1)
                ).first()
                is None,
                "Every registered load session must be loaded before audit freeze",
            )
            current = fence.check(db)
            _, target, children, masters = lock_and_tables(db, plan, checkpoint)
            require(
                schema_fingerprint([target, *children.values(), *masters.values()])
                == session["schema_sha256"]
                and baseline.anchor(db, masters, current) == session["masters_sha256"],
                "Audit schema/master universe changed before freeze",
            )
            snapshot = (
                fence.freeze(db)
                if current["phase"] == "loading"
                else fence.check_audit(db, current["audit_epoch"])
            )
            if session["state"] == "loaded":
                db.execute(
                    sessions.update()
                    .where(sessions.c.id == session_id)
                    .values(
                        state="auditing",
                        audit_epoch=snapshot["epoch"],
                        audit_next_sha256=plan["first_batch_sha256"],
                    )
                )
            else:
                require(session["audit_epoch"] == snapshot["epoch"], "Audit epoch changed")
            checkpoint()
        return {
            "session_id": session_id,
            "audit_epoch": snapshot["epoch"],
            "source_accesses": 0,
            "phase": "auditing" if session["state"] == "loaded" else session["state"],
        }
    finally:
        engine.dispose()


def frozen_batch_inputs(batch_path, pages):
    from .restore_inputs import frozen

    paths = [batch_path]
    for _, folder, manifest, _ in pages:
        paths.append(folder / "manifest.json")
        paths.extend(folder / f"{number:04d}.parquet" for number in range(len(manifest["files"])))
    return {str(path): frozen(path) for path in paths}


def audit_restore_batch(
    database_url, plan, plan_sha256, session_id, batch_path, batch_sha256, *, checkpoint
):
    """Bounded final typed readback. No target data DML; audit journal commits atomically."""
    from sqlalchemy import select

    from . import restore_baseline as baseline
    from . import restore_fence as fence
    from .restore_inputs import frozen

    validate_plan(plan, plan_sha256)
    frozen(batch_path)
    descriptor, pages = load_batch(batch_path, batch_sha256, plan, checkpoint)
    input_pins = frozen_batch_inputs(batch_path, pages)
    engine = connection(database_url)
    try:
        with engine.begin() as db:
            admission(db)
            sessions, batches, page_journal = journals(db)
            audits = audit_journal(db)
            session = (
                db.execute(select(sessions).where(sessions.c.id == session_id).with_for_update())
                .mappings()
                .one()
            )
            require(
                session["plan_sha256"] == plan_sha256
                and session["target_sha256"] == target_identity(db)
                and session["scope_sha256"] == descriptor["scope_sha256"]
                and session["state"] in {"auditing", "verified"},
                "Frozen audit session binding required",
            )
            snapshot = fence.check_audit(db, session["audit_epoch"])
            spec, target, children, masters = lock_and_tables(db, plan, checkpoint)
            require(
                schema_fingerprint([target, *children.values(), *masters.values()])
                == session["schema_sha256"]
                and baseline.anchor(db, masters, snapshot) == session["masters_sha256"],
                "Audit schema/master baseline changed",
            )
            loaded = (
                db.execute(
                    select(batches).where(
                        batches.c.session_id == session_id, batches.c.sha256 == batch_sha256
                    )
                )
                .mappings()
                .one_or_none()
            )
            require(
                loaded is not None and loaded["sequence"] == descriptor["sequence"],
                "Audit batch was never loaded",
            )
            previous = (
                db.execute(
                    select(audits).where(
                        audits.c.session_id == session_id, audits.c.batch_sha256 == batch_sha256
                    )
                )
                .mappings()
                .one_or_none()
            )
            replay = previous is not None
            require(
                (
                    replay
                    and previous["sequence"] == descriptor["sequence"]
                    and previous["epoch"] == session["audit_epoch"]
                )
                or (
                    not replay
                    and session["state"] == "auditing"
                    and session["audit_next_sha256"] == batch_sha256
                    and session["audit_batches"] == descriptor["sequence"]
                ),
                "Audit chain sequence mismatch",
            )
            batch_proof = hashlib.sha256()
            row_count = 0
            for page in pages:
                pin, _, _, candidate = page
                recorded = (
                    db.execute(
                        select(page_journal).where(
                            page_journal.c.session_id == session_id,
                            page_journal.c.candidate_id == candidate.identity,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                require(
                    recorded is not None
                    and recorded["batch_sha256"] == batch_sha256
                    and recorded["manifest_sha256"] == pin["manifest_sha256"],
                    "Audit page was never loaded",
                )
                proof = hashlib.sha256()
                count = 0
                for body in page_rows(page, spec, checkpoint):
                    raw = check_row(db, target, children, spec, body, plan["organization"])
                    proof.update(len(raw).to_bytes(8, "big") + raw)
                    count += 1
                require(
                    count == recorded["rows"] == candidate.rows
                    and proof.hexdigest() == recorded["proof_sha256"],
                    "Final typed page audit differs from loaded archive",
                )
                batch_proof.update(
                    encode(
                        {
                            "candidate": candidate.identity,
                            "manifest": pin["manifest_sha256"],
                            "proof": proof.hexdigest(),
                            "rows": count,
                        }
                    )
                )
                row_count += count
            proof_sha = batch_proof.hexdigest()
            require(proof_sha == loaded["proof_sha256"], "Final typed batch proof differs")
            if replay:
                require(
                    previous["proof_sha256"] == proof_sha
                    and previous["pages"] == len(pages)
                    and previous["rows"] == row_count,
                    "Replayed audit proof differs",
                )
            else:
                page_count = session["audit_pages"] + len(pages)
                total_rows = session["audit_rows"] + row_count
                terminal = descriptor["next_sha256"] is None
                require(
                    page_count <= plan["expected_pages"]
                    and total_rows <= plan["expected_rows"]
                    and (
                        not terminal
                        or (
                            page_count == plan["expected_pages"] == session["pages"]
                            and total_rows == plan["expected_rows"] == session["rows"]
                            and session["audit_batches"] + 1 == session["batches"]
                        )
                    )
                    and (
                        terminal
                        or (
                            page_count < plan["expected_pages"]
                            and total_rows < plan["expected_rows"]
                        )
                    ),
                    "Final audit chain truncated or totals differ",
                )
                db.execute(
                    audits.insert().values(
                        session_id=session_id,
                        batch_sha256=batch_sha256,
                        sequence=descriptor["sequence"],
                        epoch=session["audit_epoch"],
                        pages=len(pages),
                        rows=row_count,
                        proof_sha256=proof_sha,
                    )
                )
                db.execute(
                    sessions.update()
                    .where(sessions.c.id == session_id)
                    .values(
                        state="verified" if terminal else "auditing",
                        audit_next_sha256=descriptor["next_sha256"],
                        audit_pages=page_count,
                        audit_rows=total_rows,
                        audit_batches=session["audit_batches"] + 1,
                    )
                )
            require(
                frozen_batch_inputs(batch_path, pages) == input_pins,
                "Frozen input identity changed during audit",
            )
            fresh, fresh_pages = load_batch(batch_path, batch_sha256, plan, checkpoint)
            require(
                fresh == descriptor
                and [page[2] for page in fresh_pages] == [page[2] for page in pages],
                "Frozen audit input pins changed",
            )
            fence.check_audit(db, session["audit_epoch"])
            checkpoint()
            result = (
                db.execute(select(sessions).where(sessions.c.id == session_id)).mappings().one()
            )
            all_verified = (
                db.execute(
                    select(sessions.c.id).where(sessions.c.state != "verified").limit(1)
                ).first()
                is None
            )
        return {
            "session_id": session_id,
            "phase": result["state"],
            "replayed": replay,
            "audited_pages": result["audit_pages"],
            "audited_rows": result["audit_rows"],
            "session_content_audit_complete": result["state"] == "verified",
            "all_registered_sessions_verified": all_verified,
            "audit_epoch": result["audit_epoch"],
            "source_accesses": 0,
            "target_data_mutations": 0,
            "production_ready": False,
        }
    finally:
        engine.dispose()
