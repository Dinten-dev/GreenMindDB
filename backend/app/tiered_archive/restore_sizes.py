"""Conservative PG15 master-row admission before any value transfer.

No JSON/JSONB expansion or compressed pg_column_size estimate is accepted.
Unknown non-NULL types need a separately proved size/encoding contract.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    Uuid,
    and_,
    case,
    cast,
    func,
    literal,
    literal_column,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql.sqltypes import CHAR

from app.raw_archive.policy import ArchiveBlocked

MAX_FIELD_BYTES = 64 * 1024
MAX_FETCH_ESTIMATE = 1024**2


def check_encoding(db):
    if db.dialect.name != "postgresql":
        raise ArchiveBlocked("Native master size admission requires PostgreSQL")
    values = db.exec_driver_sql(
        "SELECT pg_catalog.current_setting('server_encoding'),pg_catalog.current_setting('client_encoding')"
    ).one()
    if tuple(values) != ("UTF8", "UTF8"):
        raise ArchiveBlocked("Master size contract requires verified UTF8 server/client encoding")


def raw_length(column):
    # PG15 textoctetlen/byteaoctetlen call toast_raw_datum_size directly; do not
    # cast JSON/JSONB or construct row_to_json before applying this admission.
    return func.pg_catalog.octet_length(column)


def key_contract(keys):
    for column in keys:
        kind = column.type
        if isinstance(kind, (Uuid, UUID, Integer)):
            continue
        if (
            isinstance(kind, String)
            and not isinstance(kind, (Text, CHAR))
            and kind.length is not None
            and 0 < kind.length <= 256
        ):
            continue
        return False
    return True


KNOWN_CONFIG_BYTES = 2048
KNOWN_CONFIG_CONTRACT = "direct-session-plain-json-v1"


def known_config_size(column):
    from sqlalchemy import JSON

    if (
        column.table.name != "direct_session"
        or column.table.schema not in {None, "public"}
        or column.name != "config"
        or not isinstance(column.type, JSON)
    ):
        return None
    # Metadata-only tests. No JSON conversion/output is evaluated here. Size is
    # a wire bound ONLY for actual builtin JSON stored uncompressed as text.
    admitted = and_(
        func.pg_catalog.pg_typeof(column)
        == literal_column("'pg_catalog.json'::pg_catalog.regtype"),
        cast(func.pg_catalog.current_setting("server_version_num"), Integer).between(
            150000, 159999
        ),
        func.pg_catalog.pg_column_compression(column).is_(None),
        func.pg_catalog.pg_column_size(column) <= KNOWN_CONFIG_BYTES,
    )
    return case((admitted, literal(KNOWN_CONFIG_BYTES)), else_=literal(MAX_FIELD_BYTES + 1))


def validate_known_row(master, value):
    if master.name != "direct_session":
        return
    from app.raw_archive.wav_catalog import validate_row

    try:
        validate_row("direct", "direct_session", value)
        config = value["config"]
        if (
            config["device_id"] != value["device_id"]
            or config["session_id"] != value["id"]
            or value["mode"] not in {"DIRECT", "DUAL"}
        ):
            raise ValueError("identity")
    except Exception:
        raise ArchiveBlocked("Bounded Direct session scientific contract failed") from None


def column_contract(column):
    kind = column.type
    known = known_config_size(column)
    if known is not None:
        return known
    if isinstance(kind, Enum):
        maximum = max((len(value.encode("utf8")) for value in kind.enums), default=0)
        return literal(maximum) if maximum <= MAX_FIELD_BYTES else None
    if isinstance(kind, CHAR):
        return None
    if isinstance(kind, (String, LargeBinary)):
        return raw_length(column)
    if isinstance(kind, (Uuid, UUID, Boolean, Integer, Float, Date)) and not isinstance(
        kind, DateTime
    ):
        return literal(64)
    if isinstance(kind, DateTime) and kind.timezone:
        return literal(128)
    if (
        isinstance(kind, Numeric)
        and kind.precision is not None
        and kind.scale is not None
        and 0 < kind.precision <= 38
        and 0 <= kind.scale <= 38
    ):
        return literal(256)
    return None


def next_admitted(db, master, cursor):
    keys = list(master.primary_key.columns)
    if not keys:
        raise ArchiveBlocked("Master baseline requires deterministic primary keys")
    sensitive = any(
        any(word in column.name.lower() for word in ("hash", "password", "token", "email"))
        for column in keys
    )
    if sensitive or not key_contract(keys):
        # Empty unsupported tables may finish without transferring any PK/body.
        if db.execute(select(literal(1)).select_from(master).limit(1)).first() is None:
            return None
        if sensitive:
            raise ArchiveBlocked(
                "Private master PK cursor requires separate protected restore evidence"
            )
        raise ArchiveBlocked("Master PK lacks a bounded native size contract")
    flags = []
    estimate = literal(256)
    for column in master.columns:
        size = column_contract(column)
        if size is None:
            flags.append(column.is_(None))
            estimate = estimate + 128
        else:
            size = case((column.is_(None), literal(0)), else_=cast(size, BigInteger))
            flags.append(size <= MAX_FIELD_BYTES)
            estimate = estimate + size * 8 + 256 + len(column.name.encode("utf8")) * 8
    admitted = and_(*flags, estimate <= MAX_FETCH_ESTIMATE)
    # Only booleans, bounded integers and already admitted small PKs cross the
    # wire here. Unsupported/oversized values are never selected or encoded.
    query = select(
        admitted.label("admitted"),
        estimate.label("estimate"),
        *(case((admitted, column), else_=None).label(column.name) for column in keys),
    ).select_from(master)
    if cursor is not None:
        query = query.where(tuple_(*keys) > tuple_(*cursor))
    metadata = db.execute(query.order_by(*keys).limit(1)).mappings().one_or_none()
    if metadata is None:
        return None
    if metadata["admitted"] is not True:
        raise ArchiveBlocked("Master row type or raw byte size lacks a bounded contract")
    return [metadata[column.name] for column in keys], int(metadata["estimate"]), admitted


def fetch_one(db, master, keys, admitted):
    query = (
        select(master)
        .where(
            and_(
                *(
                    column == value
                    for column, value in zip(master.primary_key.columns, keys, strict=True)
                ),
                admitted,
            )
        )
        .limit(1)
    )
    with db.execute(query.execution_options(yield_per=1)) as result:
        value = result.mappings().one_or_none()
        if value is None:
            raise ArchiveBlocked("Admitted master row changed before bounded fetch")
        return dict(value)
