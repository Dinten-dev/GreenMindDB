"""Explicit SQLite-only metadata/encoding adapter, never a native PG bound proof."""

from sqlalchemy import Integer, LargeBinary, String, cast, func

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
