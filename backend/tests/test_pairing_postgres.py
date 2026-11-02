"""Pairing locks and schema capability upgrade on real isolated PostgreSQL."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.gateway_auth import gateway_key_digest
from app.models.master import Gateway, Sensor, Zone
from app.models.pairing import PairingCode
from app.models.user import Organization, Role, User
from app.pairing_upgrade import pairing_status, prepare_pairing
from app.schemas.gateway import RegisterGatewayRequest
from app.services.gateway_service import register_gateway, register_sensor

pytestmark = pytest.mark.integration


@pytest.fixture
def pg_pairing():
    url = os.environ.get("GATEWAY_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Dedicated local PostgreSQL not configured")
    parsed = make_url(url)
    assert parsed.host in {"127.0.0.1", "localhost"} and parsed.database == "direct_test"
    schema = "typed_pairing_" + uuid4().hex
    control = create_engine(url)
    with control.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        for model in (Organization, User, Zone, Gateway, Sensor, PairingCode):
            model.__table__.create(engine)
        with Session(engine) as db:
            org = Organization(name="Synthetic pairing")
            db.add(org)
            db.flush()
            user = User(
                email="synthetic@example.invalid",
                password_hash="unused",
                role=Role.ADMIN,
                organization_id=org.id,
            )
            zone = Zone(name="Synthetic", organization_id=org.id)
            db.add_all([user, zone])
            db.flush()
            gateway = Gateway(
                zone_id=zone.id,
                hardware_id="existing",
                api_key_hash=gateway_key_digest("synthetic-current-key"),
            )
            db.add(gateway)
            db.commit()
            data = {"zone": zone.id, "user": user.id, "gateway": gateway.id}
        yield engine, data
    finally:
        engine.dispose()
        with control.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()


def _code(engine, data, code="PG1234", purpose="gateway", seconds=600):
    with Session(engine) as db:
        db.add(
            PairingCode(
                code=code,
                purpose=purpose,
                zone_id=data["zone"],
                created_by_user_id=data["user"],
                expires_at=datetime.now(UTC) + timedelta(seconds=seconds),
            )
        )
        db.commit()
    return code


@pytest.mark.parametrize("purpose", ["gateway", "sensor"])
def test_only_one_concurrent_redemption_commits(pg_pairing, purpose):
    engine, data = pg_pairing
    code = _code(engine, data, purpose=purpose)
    start = threading.Barrier(4)

    def attempt(number):
        with Session(engine) as db:
            gateway = db.get(Gateway, data["gateway"])
            start.wait(timeout=10)
            try:
                if purpose == "gateway":
                    register_gateway(
                        db, RegisterGatewayRequest(code=code, hardware_id=f"parallel-{number}")
                    )
                else:
                    register_sensor(db, gateway, f"AA:BB:CC:00:00:{number:02X}", code)
                return 201
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(attempt, range(4)))
    assert sorted(results) == [201, 400, 400, 400]
    with Session(engine) as db:
        assert db.query(PairingCode).filter_by(code=code).one().used_at is not None
        if purpose == "gateway":
            assert db.query(Gateway).filter(Gateway.hardware_id.like("parallel-%")).count() == 1
        else:
            assert db.query(Sensor).count() == 1


def test_concurrent_reprovisioning_cannot_reuse_stale_key(pg_pairing):
    engine, data = pg_pairing
    codes = [_code(engine, data, code=f"PG000{n}") for n in range(2)]
    start = threading.Barrier(2)

    def attempt(code):
        with Session(engine) as db:
            # Both sessions cache the old Gateway before acquiring any row lock.
            db.get(Gateway, data["gateway"])
            start.wait(timeout=10)
            try:
                register_gateway(
                    db,
                    RegisterGatewayRequest(code=code, hardware_id="existing"),
                    "synthetic-current-key",
                )
                return 201
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, codes))
    assert sorted(results) == [201, 409]
    with Session(engine) as db:
        assert db.query(PairingCode).filter(PairingCode.used_at.is_not(None)).count() == 1


@pytest.mark.parametrize("operation", ["rotate", "enroll"])
def test_sensor_authentication_is_revalidated_after_gateway_change(pg_pairing, operation):
    from app.gateway_auth import get_password_hash
    from app.gateway_key_migration import enroll_key

    engine, data = pg_pairing
    sensor_code = _code(engine, data, code="SENS01", purpose="sensor")
    gateway_code = _code(engine, data, code="GATE01")
    if operation == "enroll":
        with Session(engine) as db:
            db.get(Gateway, data["gateway"]).api_key_hash = get_password_hash(
                "synthetic-current-key"
            )
            db.commit()
    with Session(engine) as authenticated:
        gateway = authenticated.get(Gateway, data["gateway"])
        with Session(engine) as concurrent:
            if operation == "rotate":
                register_gateway(
                    concurrent,
                    RegisterGatewayRequest(code=gateway_code, hardware_id="existing"),
                    "synthetic-current-key",
                )
            else:
                assert enroll_key(concurrent, data["gateway"], "synthetic-current-key")
                concurrent.commit()
        if operation == "rotate":
            with pytest.raises(HTTPException) as error:
                register_sensor(
                    authenticated,
                    gateway,
                    "AA:BB:CC:00:00:FF",
                    sensor_code,
                    "synthetic-current-key",
                )
            assert error.value.status_code == 401
        else:
            assert (
                register_sensor(
                    authenticated,
                    gateway,
                    "AA:BB:CC:00:00:FF",
                    sensor_code,
                    "synthetic-current-key",
                )["status"]
                == "ok"
            )
    with Session(engine) as db:
        assert (db.query(PairingCode).filter_by(code=sensor_code).one().used_at is not None) == (
            operation == "enroll"
        )
        assert db.query(Sensor).count() == (0 if operation == "rotate" else 1)


@pytest.mark.parametrize("purpose", ["gateway", "sensor"])
def test_identity_collision_rolls_back_losing_code(pg_pairing, purpose):
    engine, data = pg_pairing
    codes = [_code(engine, data, code=f"PG000{n}", purpose=purpose) for n in range(2)]
    start = threading.Barrier(2)

    def attempt(code):
        with Session(engine) as db:
            gateway = db.get(Gateway, data["gateway"])
            start.wait(timeout=10)
            try:
                if purpose == "gateway":
                    register_gateway(
                        db, RegisterGatewayRequest(code=code, hardware_id="same-new-hardware")
                    )
                else:
                    register_sensor(db, gateway, "AA:BB:CC:00:00:AA", code)
                return 201
            except HTTPException as error:
                return error.status_code

    # A same-Gateway sensor registration using a fresh code is intentionally valid.
    # Gateway identity creation is unique; a conflicting attempt must preserve its code.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, codes))
    assert sorted(results) == ([201, 409] if purpose == "gateway" else [201, 201])
    with Session(engine) as db:
        assert db.query(PairingCode).filter(PairingCode.used_at.is_not(None)).count() == (
            1 if purpose == "gateway" else 2
        )
        if purpose == "sensor":
            assert db.query(Sensor).count() == 1


def test_expiry_rechecked_after_blocked_code_lock(pg_pairing):
    engine, data = pg_pairing
    code = _code(engine, data, seconds=600)
    ready = threading.Event()
    pid_holder = []
    blocker = engine.connect()
    transaction = blocker.begin()
    blocker.execute(
        text("SELECT id FROM pairing_code WHERE code = :code FOR UPDATE"), {"code": code}
    )

    def attempt():
        with Session(engine) as db:
            pid_holder.append(db.execute(text("SELECT pg_backend_pid()")).scalar_one())
            ready.set()
            try:
                register_gateway(db, RegisterGatewayRequest(code=code, hardware_id="expired-wait"))
                return 201
            except HTTPException as error:
                return error.status_code

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(attempt)
            try:
                assert ready.wait(10)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    with engine.connect() as check:
                        waiting = check.execute(
                            text(
                                "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"
                            ),
                            {"pid": pid_holder[0]},
                        ).scalar_one()
                    if waiting:
                        break
                    time.sleep(0.02)
                assert waiting, "Redemption must wait for the row lock"
                blocker.execute(
                    text(
                        "UPDATE pairing_code SET expires_at = clock_timestamp() - interval '1 second' WHERE code = :code"
                    ),
                    {"code": code},
                )
            finally:
                transaction.commit()
            assert pending.result(timeout=10) == 400
    finally:
        if transaction.is_active:
            transaction.rollback()
        blocker.close()
    with Session(engine) as db:
        assert db.query(Gateway).filter_by(hardware_id="expired-wait").count() == 0
        assert db.query(PairingCode).filter_by(code=code).one().used_at is None


@pytest.mark.parametrize("revision", ["0023", "0024", "0025"])
def test_upgrade_invalidates_untyped_codes_without_changing_revision(pg_pairing, revision):
    engine, data = pg_pairing
    used = datetime.now(UTC) - timedelta(days=1)
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE pairing_code DROP CONSTRAINT ck_pairing_code_purpose_v1")
        )
        connection.execute(text("ALTER TABLE pairing_code DROP COLUMN purpose"))
        connection.execute(
            text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
        )
        connection.execute(
            text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": revision}
        )
        for n, state in enumerate(("unused", "expired", "used")):
            connection.execute(
                text(
                    "INSERT INTO pairing_code (id, code, zone_id, created_by_user_id, expires_at, used_at) VALUES (:id, :code, :zone, :user, :expiry, :used)"
                ),
                {
                    "id": uuid4(),
                    "code": f"OLD00{n}",
                    "zone": data["zone"],
                    "user": data["user"],
                    "expiry": used
                    if state == "expired"
                    else datetime.now(UTC) + timedelta(minutes=10),
                    "used": used if state == "used" else None,
                },
            )
    prepare_pairing(engine)
    typed = _code(engine, data, code="NEW123")
    prepare_pairing(engine)
    assert dict(pairing_status(engine)) == {"gateway": 1, "legacy": 3}
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == revision
        )
        assert (
            connection.execute(
                text("SELECT used_at FROM pairing_code WHERE code='OLD002'")
            ).scalar_one()
            == used
        )
        assert connection.execute(text("SELECT count(*) FROM gateway")).scalar_one() == 1
    with Session(engine) as db:
        assert all(
            pc.purpose == "legacy" and pc.used_at
            for pc in db.query(PairingCode).filter(PairingCode.code.like("OLD%"))
        )
        assert db.query(PairingCode).filter_by(code=typed).one().used_at is None
        for code in ("OLD000", "OLD001", "OLD002"):
            with pytest.raises(HTTPException) as error:
                register_gateway(db, RegisterGatewayRequest(code=code, hardware_id="old-code"))
            assert error.value.status_code == 400
    # An old issuer that omits purpose cannot create usable codes anymore.
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO pairing_code (id, code, zone_id, created_by_user_id, expires_at) VALUES (:id, 'OLDNEW', :zone, :user, clock_timestamp() + interval '10 minutes')"
            ),
            {"id": uuid4(), "zone": data["zone"], "user": data["user"]},
        )


def test_unknown_purpose_aborts_upgrade_atomically(pg_pairing):
    engine, data = pg_pairing
    _code(engine, data)
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE pairing_code DROP CONSTRAINT ck_pairing_code_purpose_v1")
        )
        connection.execute(text("UPDATE pairing_code SET purpose='unknown'"))
    with pytest.raises(ValueError, match="Unknown"):
        prepare_pairing(engine)
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT purpose FROM pairing_code")).scalar_one() == "unknown"
        )
