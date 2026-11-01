"""Explicit test-only physical SQLite adapter for the PG restore fence."""

from sqlalchemy import MetaData, text

from app.tiered_archive import restore_fence as fence
from app.tiered_archive.codec import digest


def identity(db):
    return db.info.get(
        "restore_fence_identity",
        {
            "name": "unit_restore_fence",
            "oid": 1,
            "address": "127.0.0.1",
            "port": 5433,
        },
    )


def target_sha256(db):
    return digest(identity(db))


def install_sqlite_adapter(monkeypatch):
    original_tables = fence._control_tables

    def control_tables():
        metadata = MetaData()
        return tuple(t.to_metadata(metadata, schema=None) for t in original_tables())

    def origin(db):
        assert db.dialect.name == "sqlite"
        db.connection.driver_connection.create_function(
            "gm_restore_origin", 0, lambda: db.info.get("fence_origin", "origin")
        )
        fence._require(
            db.info.get("fence_origin", "origin") == "origin",
            "Restore mutation fence requires origin replication role",
        )

    def presence(db):
        names = set(db.execute(text("SELECT name FROM sqlite_master")).scalars())
        return tuple(name in names for name in (fence.STATE, fence.REGISTRY, fence.FUNCTION))

    def relation_pin(db, name):
        rows = db.execute(
            text(
                "SELECT rowid,type,name,tbl_name,rootpage,sql FROM sqlite_master WHERE name=:name OR tbl_name=:name ORDER BY type,name"
            ),
            {"name": name},
        ).all()
        fence._require(
            any(row[1] == "table" and row[2] == name for row in rows), "Restore relation missing"
        )
        return digest([list(row) for row in rows])

    def function_pin(db):
        value = db.execute(
            text("SELECT sql FROM sqlite_master WHERE type='view' AND name=:name"),
            {"name": fence.FUNCTION},
        ).scalar_one_or_none()
        fence._require(value is not None, "Restore mutation function identity missing")
        return digest(value)

    def install_function(db):
        db.exec_driver_sql("CREATE VIEW " + fence.FUNCTION + " AS SELECT 'test-only-v1' AS body")

    def install_triggers(db, name, **_options):
        for operation in ("INSERT", "UPDATE", "DELETE"):
            delta = {"INSERT": 1, "UPDATE": 0, "DELETE": -1}[operation]
            db.exec_driver_sql(f'''CREATE TRIGGER "{fence.ROW_TRIGGER}_{name}_{operation.lower()}"
            AFTER {operation} ON "{name}" BEGIN
              SELECT RAISE(ABORT,'restore_fence_origin_required') WHERE gm_restore_origin()<>'origin';
              SELECT RAISE(ABORT,'restore_fence_frozen') WHERE
                (SELECT phase FROM {fence.STATE} WHERE id=1)<>'loading';
              UPDATE {fence.STATE} SET epoch=epoch+1,
                master_epoch=master_epoch+(SELECT master FROM {fence.REGISTRY} WHERE table_name='{name}') WHERE id=1;
              UPDATE {fence.REGISTRY} SET rows=rows+({delta})
                WHERE table_name='{name}' AND master=0;
            END''')

    monkeypatch.setattr(fence, "_postgres", origin)
    monkeypatch.setattr(fence, "_identity", identity)
    monkeypatch.setattr(fence, "_control_tables", control_tables)
    monkeypatch.setattr(fence, "_control_presence", presence)
    monkeypatch.setattr(fence, "_relation_pin", relation_pin)
    monkeypatch.setattr(fence, "_function_pin", function_pin)
    monkeypatch.setattr(fence, "_install_function", install_function)
    monkeypatch.setattr(fence, "_install_triggers", install_triggers)
    if hasattr(fence, "_coverage"):
        monkeypatch.setattr(fence, "_coverage", lambda *_args, **_kwargs: {})
    return target_sha256
