"""Real PostgreSQL index and credential compare-and-swap in an isolated schema."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app import gateway_auth
from app.gateway_key_migration import enroll_key, prepare_index
from app.models.master import Gateway, Zone
from app.models.user import Organization

pytestmark = pytest.mark.integration


@pytest.fixture
def pg_gateway():
    url = os.environ.get("GATEWAY_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Dedicated local PostgreSQL not configured")
    parsed = make_url(url)
    assert parsed.host in {"127.0.0.1", "localhost"} and parsed.database == "direct_test"
    schema = "gateway_bounds_" + uuid4().hex
    control = create_engine(url)
    with control.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        for model in (Organization, Zone, Gateway):
            model.__table__.create(engine)
        with Session(engine) as db:
            org = Organization(name="Synthetic gateway migration")
            db.add(org)
            db.flush()
            zone = Zone(name="Synthetic", organization_id=org.id)
            db.add(zone)
            db.flush()
            gateway = Gateway(
                zone_id=zone.id,
                hardware_id="migration-test",
                api_key_hash=gateway_auth.get_password_hash("synthetic-legacy-secret"),
            )
            db.add(gateway)
            db.commit()
            identity = gateway.id
        yield engine, identity
    finally:
        engine.dispose()
        with control.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()


def test_concurrent_index_preparation_and_enrollment(pg_gateway):
    engine, identity = pg_gateway
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_gateway_api_key_hash"))
    prepare_index(engine)
    prepare_index(engine)
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT indisvalid FROM pg_index WHERE indexrelid = 'ix_gateway_api_key_hash'::regclass"
            )
        ).scalar_one()
    with Session(engine) as db:
        assert enroll_key(db, identity, "synthetic-legacy-secret")
        db.commit()
    gateway_auth.clear_gateway_auth_cache()
    with Session(engine) as db:
        assert (
            gateway_auth.authenticate_gateway_api_key(db, "synthetic-legacy-secret").id == identity
        )
        assert not enroll_key(db, identity, "synthetic-legacy-secret")


@pytest.mark.parametrize("change", ["disable", "rotate"])
def test_enrollment_cannot_overwrite_concurrent_revocation(pg_gateway, monkeypatch, change):
    from app import gateway_key_migration

    engine, identity = pg_gateway
    verify = gateway_key_migration._verify_gateway_key

    def concurrent_change(gateway, key):
        assert verify(gateway, key)
        with engine.begin() as connection:
            values = (
                {"is_active": False}
                if change == "disable"
                else {"api_key_hash": gateway_auth.gateway_key_digest("replacement-key")}
            )
            connection.execute(update(Gateway).where(Gateway.id == identity).values(**values))
        return True

    monkeypatch.setattr(gateway_key_migration, "_verify_gateway_key", concurrent_change)
    with Session(engine) as db:
        with pytest.raises(ValueError, match="changed"):
            enroll_key(db, identity, "synthetic-legacy-secret")
        db.rollback()
    with Session(engine) as db:
        gateway = db.get(Gateway, identity)
        if change == "disable":
            assert gateway.is_active is False
        else:
            assert gateway.api_key_hash == gateway_auth.gateway_key_digest("replacement-key")
