"""Explicit creation and bounded connections to the separate Direct database."""

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def make_engine(settings):
    url = settings.database_url.get_secret_value()
    if not url:
        raise ValueError("DIRECT_DATABASE_URL is required only for Direct services")
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})

        @event.listens_for(engine, "connect")
        def configure_sqlite(conn, _):
            conn.isolation_level = None
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=FULL")

        @event.listens_for(engine, "begin")
        def begin_sqlite(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE")

        return engine
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=2,
        pool_timeout=5,
        connect_args={
            "options": "-c statement_timeout=30000 -c lock_timeout=5000 -c synchronous_commit=on"
        },
    )


@contextmanager
def transaction(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db, db.begin():
        yield db
