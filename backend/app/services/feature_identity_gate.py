"""Pause feature writes until the physical six-column identity is usable.

No migration/head inference or cached environment flag substitutes for catalog
evidence. Transaction locks fence the existing online operator/constraint switch;
callers recheck after every commit before further writes.
"""

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.raw_archive.feature_identity_upgrade import LOCK
from app.raw_archive.health import SafetyPause

IDENTITY = (
    "wav_file_id",
    "extractor_version",
    "calibration_version",
    "parameter_hash",
    "source_sha256",
    "feature_checksum",
)


class FeatureIdentityWait(SafetyPause):
    def __init__(self):
        super().__init__("feature_identity_schema_wait")


_PG_INDEXES = """
SELECT i.indisunique AS unique, i.indisprimary AS primary,
       i.indisvalid AS valid, i.indisready AS ready, i.indislive AS live,
       i.indimmediate AS immediate, i.indisexclusion AS exclusion,
       i.indnatts AS attributes, i.indnkeyatts AS key_attributes,
       i.indexprs IS NULL AS no_expressions, i.indpred IS NULL AS no_predicate,
       am.amname AS method,
       ARRAY(SELECT a.attname FROM unnest(i.indkey::smallint[]) WITH ORDINALITY k(num,pos)
         JOIN pg_catalog.pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.num
         AND NOT a.attisdropped ORDER BY k.pos) AS columns,
       ARRAY(SELECT a.attnotnull FROM unnest(i.indkey::smallint[]) WITH ORDINALITY k(num,pos)
         JOIN pg_catalog.pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.num
         AND NOT a.attisdropped ORDER BY k.pos) AS not_null,
       ARRAY(SELECT o.opcdefault AND ns.nspname='pg_catalog'
         FROM unnest(i.indclass::oid[]) WITH ORDINALITY k(num,pos)
         JOIN pg_catalog.pg_opclass o ON o.oid=k.num
         JOIN pg_catalog.pg_namespace ns ON ns.oid=o.opcnamespace ORDER BY k.pos) AS default_ops,
       i.indoption::smallint[] AS options,
       ARRAY(SELECT k.num FROM unnest(i.indcollation::oid[]) WITH ORDINALITY k(num,pos)
         ORDER BY k.pos) = ARRAY(SELECT a.attcollation
         FROM unnest(i.indkey::smallint[]) WITH ORDINALITY k(num,pos)
         JOIN pg_catalog.pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.num
         AND NOT a.attisdropped ORDER BY k.pos) AS same_collations
FROM pg_catalog.pg_class t
JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace
JOIN pg_catalog.pg_index i ON i.indrelid=t.oid
JOIN pg_catalog.pg_class ix ON ix.oid=i.indexrelid
JOIN pg_catalog.pg_am am ON am.oid=ix.relam
WHERE n.nspname='public' AND t.relname='wav_feature_version'
  AND t.relkind='r' AND t.relpersistence='p'
  AND t.oid=pg_catalog.to_regclass('wav_feature_version')
ORDER BY i.indexrelid LIMIT 65
"""


def _postgres_ready(rows):
    if not rows or len(rows) > 64:
        return False
    ready = False
    for row in rows:
        if row["exclusion"] is True:
            return False
        if row["unique"] is not True:
            continue
        if row["primary"] is True and tuple(row["columns"]) == ("id",):
            continue
        # Unknown/partial/expression/old four-column uniques may reject distinct
        # six-column results. They must never be treated as a harmless duplicate.
        if not (
            tuple(row["columns"]) == IDENTITY
            and all(
                row[name] is True
                for name in (
                    "valid",
                    "ready",
                    "live",
                    "immediate",
                    "no_expressions",
                    "no_predicate",
                    "same_collations",
                )
            )
            and row["exclusion"] is False
            and row["method"] == "btree"
            and row["attributes"] == row["key_attributes"] == len(IDENTITY)
            and list(row["not_null"]) == [True] * len(IDENTITY)
            and list(row["default_ops"]) == [True] * len(IDENTITY)
            and list(row["options"]) == [0] * len(IDENTITY)
        ):
            return False
        ready = True
    return ready


def _sqlite_ready(connection):
    if connection.exec_driver_sql(
        "SELECT 1 FROM temp.sqlite_master WHERE name='wav_feature_version'"
    ).first():
        return False
    table = connection.exec_driver_sql(
        "SELECT type FROM main.sqlite_master WHERE name='wav_feature_version'"
    ).all()
    if table != [("table",)]:
        return False
    columns = connection.exec_driver_sql("PRAGMA main.table_xinfo('wav_feature_version')").all()
    if any(not any(c[1] == name and c[3] == 1 and c[6] == 0 for c in columns) for name in IDENTITY):
        return False
    indexes = connection.exec_driver_sql("PRAGMA main.index_list('wav_feature_version')").all()
    if len(indexes) > 64:
        return False
    ready = False
    for index in indexes:
        if index[2] != 1:
            continue
        name = '"' + index[1].replace('"', '""') + '"'
        keys = [
            r
            for r in connection.exec_driver_sql("PRAGMA main.index_xinfo(" + name + ")").all()
            if r[5] == 1
        ]
        names = tuple(r[2] for r in keys)
        if index[3] == "pk" and names == ("id",):
            continue
        if not (
            names == IDENTITY
            and index[4] == 0
            and all(r[1] >= 0 and r[3] == 0 and r[4] == "BINARY" for r in keys)
        ):
            return False
        ready = True
    return ready


def require_feature_identity(db):
    """Check without ORM autoflush; leave transaction ownership with the caller."""
    try:
        connection = db.connection()
        if connection.dialect.name == "postgresql":
            held = connection.execute(
                text("SELECT pg_try_advisory_xact_lock_shared(:namespace,:identity)"),
                {"namespace": LOCK[0], "identity": LOCK[1]},
            ).scalar_one()
            if held is not True:
                raise FeatureIdentityWait()
            connection.exec_driver_sql(
                "LOCK TABLE ONLY public.wav_feature_version IN ACCESS SHARE MODE NOWAIT"
            )
            ready = _postgres_ready(connection.execute(text(_PG_INDEXES)).mappings().all())
        elif connection.dialect.name == "sqlite":
            ready = _sqlite_ready(connection)
        else:
            ready = False
    except SQLAlchemyError:
        raise FeatureIdentityWait() from None
    if not ready:
        raise FeatureIdentityWait()
