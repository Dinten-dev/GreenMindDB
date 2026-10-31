"""Explicit scientific reads reuse the existing WAV-only column contract."""

from types import SimpleNamespace

from .policy import ArchiveBlocked
from .wav_catalog import COLUMNS, validate_row


def query(db, model, kind):
    """Select named columns, so ORM additions never widen reads or archives."""
    fields = COLUMNS[kind][model.__tablename__]
    return db.query(*(getattr(model, name) for name in fields))


def one(db, model, kind, *criteria, optional=False):
    row = query(db, model, kind).filter(*criteria).one_or_none()
    if row is None:
        if optional:
            return None
        raise ArchiveBlocked("Required scientific metadata is missing")
    return SimpleNamespace(**row._mapping)


def rows(db, model, kind, *criteria):
    return [SimpleNamespace(**row._mapping) for row in query(db, model, kind).filter(*criteria)]


def scientific_row(row, kind, table):
    value = {name: getattr(row, name) for name in COLUMNS[kind][table]}
    # Reuse nested JSON allowlists too: configs/features cannot carry arbitrary
    # account, key, diagnostic-error or authentication fields into WAV Parquet.
    validate_row(kind, table, value)
    return value
