"""Explicit V4 authority scope; absence is a catalog fact, never an omission."""

import re

from sqlalchemy import inspect, text

from app.raw_archive.policy import ArchiveBlocked

from .codec import digest

VERSION = 4
FORMAT = "schema-master-v4"
AUTH_FORMAT = "greenmind.schema-master-proof-auth.v1"
AUTH_TABLES = ("access_invitation", "auth_session")
KNOWN_HEADS = {"0023", "0024", "0025"}


def need(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


def validate_scope(value):
    need(
        isinstance(value, dict)
        and set(value)
        == {"schema", "mode", "alembic_heads", "auth_relations", "auth_shapes", "catalog_sha256"},
        "Explicit version4 master schema scope required",
    )
    need(
        value["schema"] == "public"
        and type(value["alembic_heads"]) is list
        and len(value["alembic_heads"]) == 1
        and isinstance(value["alembic_heads"][0], str)
        and value["alembic_heads"][0] in KNOWN_HEADS,
        "Unsupported or ambiguous V4 Alembic head",
    )
    relations = value["auth_relations"]
    shapes = value["auth_shapes"]
    need(
        isinstance(relations, dict) and set(relations) == set(AUTH_TABLES),
        "Explicit authority relation presence required",
    )
    need(
        isinstance(shapes, dict) and set(shapes) == set(AUTH_TABLES),
        "Explicit authority catalog shapes required",
    )
    if value["mode"] == "schema0023-auth-absent":
        need(
            value["alembic_heads"] == ["0023"]
            and relations == dict.fromkeys(AUTH_TABLES, "absent")
            and shapes == dict.fromkeys(AUTH_TABLES),
            "Schema0023 authority absence is not proved",
        )
    else:
        need(
            value["mode"] == "full-auth"
            and all(isinstance(kind, str) and kind in {"r", "p"} for kind in relations.values())
            and all(
                isinstance(v, str) and re.fullmatch("[a-f0-9]{64}", v) for v in shapes.values()
            ),
            "Current authority scope must include both auth tables",
        )
    need(
        value["catalog_sha256"]
        == digest({"schema": "public", "auth_relations": relations, "auth_shapes": shapes}),
        "V4 authority catalog checksum differs",
    )
    return value


def capture_scope(db, *, checkpoint):
    checkpoint()
    schema = "public" if db.dialect.name == "postgresql" else None
    need(
        inspect(db).has_table("alembic_version", schema=schema),
        "Exact Alembic catalog is required for V4",
    )
    heads = list(
        db.execute(
            text(
                "SELECT version_num FROM "
                + ("public." if schema else "")
                + "alembic_version ORDER BY version_num"
            )
        ).scalars()
    )
    if schema:
        entries = db.execute(
            text("""SELECT c.relname,c.relkind FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname IN ('access_invitation','auth_session')
            ORDER BY c.relname""")
        ).all()
    else:
        entries = [
            (name, "r" if kind == "table" else "v")
            for name, kind in db.execute(
                text(
                    "SELECT name,type FROM sqlite_master WHERE name IN "
                    "('access_invitation','auth_session') ORDER BY name"
                )
            ).all()
        ]
    relations = dict.fromkeys(AUTH_TABLES, "absent")
    relations.update(dict(entries))
    inspector = inspect(db)
    shapes = {}
    for name, kind in relations.items():
        checkpoint()
        if kind == "absent":
            shapes[name] = None
        elif kind in {"r", "p"}:
            shapes[name] = digest(
                {
                    "columns": [
                        {**c, "type": c["type"].compile(dialect=db.dialect)}
                        for c in inspector.get_columns(name, schema=schema)
                    ],
                    "pk": inspector.get_pk_constraint(name, schema=schema),
                    "fks": sorted(
                        inspector.get_foreign_keys(name, schema=schema),
                        key=lambda v: str(v["name"]),
                    ),
                    "unique": sorted(
                        inspector.get_unique_constraints(name, schema=schema),
                        key=lambda v: str(v["name"]),
                    ),
                    "indexes": sorted(
                        inspector.get_indexes(name, schema=schema), key=lambda v: str(v["name"])
                    ),
                    "checks": sorted(
                        inspector.get_check_constraints(name, schema=schema),
                        key=lambda v: str(v["name"]),
                    ),
                }
            )
        else:
            raise ArchiveBlocked("Authority relation is not a base table")
    mode = (
        "schema0023-auth-absent" if all(v == "absent" for v in relations.values()) else "full-auth"
    )
    value = dict(
        schema="public",
        mode=mode,
        alembic_heads=heads,
        auth_relations=relations,
        auth_shapes=shapes,
        catalog_sha256=digest(
            {"schema": "public", "auth_relations": relations, "auth_shapes": shapes}
        ),
    )
    return validate_scope(value)


def names(source, scope, *, full_tables):
    need(source == "gateway", "V4 schema/master scope requires Gateway source")
    validate_scope(scope)
    return tuple(
        name
        for name in full_tables
        if scope["mode"] != "schema0023-auth-absent" or name not in AUTH_TABLES
    )
