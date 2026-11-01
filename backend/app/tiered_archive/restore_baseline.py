"""Bounded keyset baselines of already restored, mutation-fenced master tables."""

import hashlib

from app.raw_archive.policy import ArchiveBlocked

from .codec import decode, digest, encode


def journal(db):
    from sqlalchemy import BigInteger, Column, LargeBinary, MetaData, String, Table

    table = Table(
        "greenmind_restore_baseline",
        MetaData(),
        Column("name", String(64), primary_key=True),
        Column("schema_sha256", String(64), nullable=False),
        Column("master_epoch", BigInteger, nullable=False),
        Column("cursor", LargeBinary),
        Column("rows", BigInteger, nullable=False),
        Column("proof_sha256", String(64), nullable=False),
        Column("state", String(8), nullable=False),
    )
    table.create(db, checkfirst=True)
    return table


def ensure(db, masters, snapshot, schema_fingerprint):
    from sqlalchemy import select

    table = journal(db)
    for name, master in sorted(masters.items()):
        signature = schema_fingerprint([master])
        existing = db.execute(select(table).where(table.c.name == name)).mappings().one_or_none()
        if existing is not None:
            if (
                existing["schema_sha256"] != signature
                or existing["master_epoch"] != snapshot["master_epoch"]
            ):
                raise ArchiveBlocked("Restored master baseline changed")
        else:
            db.execute(
                table.insert().values(
                    name=name,
                    schema_sha256=signature,
                    master_epoch=snapshot["master_epoch"],
                    cursor=None,
                    rows=0,
                    proof_sha256=digest({"table": name, "schema_sha256": signature}),
                    state="open",
                )
            )


def anchor(db, masters, snapshot):
    from sqlalchemy import select

    table = journal(db)
    values = []
    for name in sorted(masters):
        row = db.execute(select(table).where(table.c.name == name)).mappings().one()
        if row["master_epoch"] != snapshot["master_epoch"]:
            raise ArchiveBlocked("Restored master epoch changed")
        if row["state"] != "done":
            return "0" * 64
        values.append(
            {
                key: row[key]
                for key in ("name", "schema_sha256", "master_epoch", "rows", "proof_sha256")
            }
        )
    return digest(values)


def advance(db, masters, snapshot, *, checkpoint, maximum_rows=500, maximum_bytes=8 * 1024**2):
    from sqlalchemy import select, tuple_

    if (
        type(maximum_rows) is not int
        or not 1 <= maximum_rows <= 500
        or type(maximum_bytes) is not int
        or not 1024 <= maximum_bytes <= 8 * 1024**2
    ):
        raise ArchiveBlocked("Bounded master baseline budget required")
    table = journal(db)
    for name, master in sorted(masters.items()):
        state = db.execute(select(table).where(table.c.name == name)).mappings().one()
        if state["master_epoch"] != snapshot["master_epoch"]:
            raise ArchiveBlocked("Restored master changed during baseline")
        if state["state"] == "done":
            continue
        keys = list(master.primary_key.columns)
        if not keys:
            raise ArchiveBlocked("Master baseline requires deterministic primary keys")
        query = select(master).order_by(*keys).limit(maximum_rows + 1)
        if state["cursor"] is not None:
            cursor = decode(bytes(state["cursor"]))
            query = query.where(tuple_(*keys) > tuple_(*cursor))
        count = decoded = 0
        chain, cursor, complete = state["proof_sha256"], state["cursor"], False
        with db.execute(query.execution_options(yield_per=16)) as result:
            for value in result.mappings():
                checkpoint()
                if any(
                    any(
                        word in column.name.lower()
                        for word in ("hash", "password", "token", "email")
                    )
                    for column in keys
                ):
                    raise ArchiveBlocked(
                        "Private master PK cursor requires separate protected restore evidence"
                    )
                raw = encode(dict(value))
                if count >= maximum_rows or decoded + len(raw) > maximum_bytes:
                    break
                chain = hashlib.sha256(
                    bytes.fromhex(chain) + len(raw).to_bytes(8, "big") + raw
                ).hexdigest()
                # Cursor contains only fixed scientific UUID/session/hardware PKs.
                # Other private master columns are hashed, never journalled/logged.
                cursor = encode([value[column.name] for column in keys])
                count += 1
                decoded += len(raw)
            else:
                complete = True
        if count == 0 and not complete:
            raise ArchiveBlocked("Master row exceeds bounded baseline budget")
        db.execute(
            table.update()
            .where(table.c.name == name)
            .values(
                cursor=cursor,
                rows=state["rows"] + count,
                proof_sha256=chain,
                state="done" if complete else "open",
            )
        )
        return {"table": name, "rows": count, "decoded_bytes": decoded, "table_complete": complete}
    return {"table": None, "rows": 0, "decoded_bytes": 0, "table_complete": True}
