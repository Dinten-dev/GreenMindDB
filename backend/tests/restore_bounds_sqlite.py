"""Explicit SQLite-only metadata/encoding adapter, never a native PG bound proof."""

from sqlalchemy import JSON, Integer, LargeBinary, String, case, cast, func, literal

from app.tiered_archive import restore_sizes as sizes


def install_bounds_adapter(monkeypatch):
    def encoding(db):
        assert db.dialect.name == "sqlite"

    monkeypatch.setattr(sizes, "check_encoding", encoding)
    monkeypatch.setattr(sizes, "raw_length", lambda column: func.length(cast(column, LargeBinary)))
    # Synthetic fixtures declare TEXT PKs; actual PG must use bounded/native PKs.
    monkeypatch.setattr(
        sizes,
        "key_contract",
        lambda keys: all(isinstance(column.type, (Integer, String)) for column in keys),
    )

    original_known = sizes.known_config_size

    def known(column):
        if (
            column.table.name == "direct_session"
            and column.name == "config"
            and isinstance(column.type, JSON)
        ):
            # Explicit SQLite-only uncompressed JSONtext simulation; no native
            # PostgreSQL OID/compression evidence is claimed by this adapter.
            return case(
                (
                    func.length(cast(column, LargeBinary)) <= sizes.KNOWN_CONFIG_BYTES,
                    literal(sizes.KNOWN_CONFIG_BYTES),
                ),
                else_=literal(sizes.MAX_FIELD_BYTES + 1),
            )
        return original_known(column)

    monkeypatch.setattr(sizes, "known_config_size", known)
