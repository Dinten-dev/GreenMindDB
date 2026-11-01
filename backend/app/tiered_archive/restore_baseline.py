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
    from sqlalchemy import select

    from . import restore_sizes as sizes

    if (
        type(maximum_rows) is not int
        or not 1 <= maximum_rows <= 500
        or type(maximum_bytes) is not int
        or not 1024 <= maximum_bytes <= 8 * 1024**2
    ):
        raise ArchiveBlocked("Bounded master baseline budget required")
    sizes.check_encoding(db)
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
        count = decoded = 0
        chain, cursor, complete = state["proof_sha256"], state["cursor"], False
        while count < maximum_rows:
            checkpoint()
            previous = decode(bytes(cursor)) if cursor is not None else None
            admitted = sizes.next_admitted(db, master, previous)
            if admitted is None:
                complete = True
                break
            key_values, estimate, predicate = admitted
            if decoded + estimate > maximum_bytes:
                if count:
                    break
                raise ArchiveBlocked("Master row exceeds bounded baseline budget")
            value = sizes.fetch_one(db, master, key_values, predicate)
            sizes.validate_known_row(master, value)
            raw = encode(value)
            if decoded + len(raw) > maximum_bytes:
                raise ArchiveBlocked("Master codec output exceeds declared baseline budget")
            chain = hashlib.sha256(
                bytes.fromhex(chain) + len(raw).to_bytes(8, "big") + raw
            ).hexdigest()
            cursor = encode([value[column.name] for column in keys])
            count += 1
            decoded += len(raw)
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
