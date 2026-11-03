"""Removal and provisioning serialize on a retained PostgreSQL Gateway row."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.gateway_auth import (
    authenticate_gateway_api_key,
    generate_gateway_api_key,
    set_gateway_api_key,
)
from app.models.master import Gateway, Sensor
from app.models.user import User
from app.schemas.gateway import RegisterGatewayRequest
from app.services.gateway_service import delete_gateway, register_gateway
from tests.test_pairing_postgres import _code

pytest_plugins = ["tests.test_pairing_postgres"]
pytestmark = pytest.mark.integration


def test_parallel_removals_keep_identity_and_disconnected_sensor(pg_pairing):
    engine, data = pg_pairing
    with Session(engine) as db:
        sensor = Sensor(gateway_id=data["gateway"], mac_address="AA:BB:CC:DD:00:77")
        db.add(sensor)
        db.commit()
        sensor_id = sensor.id
    start = threading.Barrier(2)

    def remove(_):
        with Session(engine) as db:
            db.execute(text("SET LOCAL lock_timeout='3s'"))
            user = db.get(User, data["user"])
            start.wait(timeout=5)
            delete_gateway(db, user, data["gateway"])
            return 204

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(remove, range(2))) == [204, 204]
    with Session(engine) as db:
        assert db.query(Gateway).count() == 1
        assert db.get(Gateway, data["gateway"]).is_active is False
        assert db.get(Gateway, data["gateway"]).api_key_hash is None
        assert db.get(Sensor, sensor_id).gateway_id == data["gateway"]
        assert db.get(Sensor, sensor_id).status == "disconnected"


def test_removal_and_reprovisioning_cannot_leave_a_live_credential(pg_pairing):
    engine, data = pg_pairing
    code = _code(engine, data, code="REMOV7")
    start = threading.Barrier(2)

    def remove():
        with Session(engine) as db:
            db.execute(text("SET LOCAL lock_timeout='3s'"))
            user = db.get(User, data["user"])
            start.wait(timeout=5)
            delete_gateway(db, user, data["gateway"])
            return 204

    def provision():
        with Session(engine) as db:
            db.execute(text("SET LOCAL lock_timeout='3s'"))
            start.wait(timeout=5)
            try:
                register_gateway(
                    db,
                    RegisterGatewayRequest(code=code, hardware_id="existing"),
                    current_api_key="synthetic-current-key",
                )
                return 201
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        removal = pool.submit(remove)
        registration = pool.submit(provision)
        assert removal.result(timeout=10) == 204
        assert registration.result(timeout=10) in {201, 403}
    with Session(engine) as db:
        assert db.query(Gateway).count() == 1
        retained = db.get(Gateway, data["gateway"])
        assert retained and retained.is_active is False and retained.api_key_hash is None


def test_revocation_is_visible_in_a_previously_loaded_orm_session(pg_pairing):
    engine, data = pg_pairing
    with Session(engine) as db:
        gateway = db.get(Gateway, data["gateway"])
        key = generate_gateway_api_key(gateway.id)
        set_gateway_api_key(gateway, key)
        db.commit()
    with Session(engine) as stale:
        loaded = stale.get(Gateway, data["gateway"])
        assert loaded.is_active
        with Session(engine) as other:
            delete_gateway(other, other.get(User, data["user"]), data["gateway"])
        with pytest.raises(HTTPException) as rejected:
            authenticate_gateway_api_key(stale, key)
        assert rejected.value.status_code in {401, 403}
