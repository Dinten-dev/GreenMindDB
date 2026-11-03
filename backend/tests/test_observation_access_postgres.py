"""Real PostgreSQL capability uniqueness, revocation and upgrade tests."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.evaluation import PlantEvaluation
from app.models.master import Gateway, Sensor, Zone
from app.models.observation import (
    PlantObservation,
    PlantObservationAccess,
    PlantObservationPhoto,
    PlantObservationSession,
)
from app.models.plant import Plant, PlantSensorAssignment
from app.models.user import Organization, Role, User
from app.observation_access_upgrade import observation_access_status, prepare_observation_access
from app.schemas.observation import PlantObservationCreate
from app.services import observation_service
from app.services.plant_service import get_or_create_observation_access, revoke_observation_access

pytestmark = pytest.mark.integration


@pytest.fixture
def pg_access():
    url = os.environ.get("GATEWAY_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Dedicated local PostgreSQL not configured")
    parsed = make_url(url)
    assert parsed.host in {"127.0.0.1", "localhost"} and parsed.database == "direct_test"
    schema = "public_access_" + uuid4().hex
    control = create_engine(url)
    with control.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url, connect_args={"options": f"-csearch_path={schema}", "application_name": schema}
    )
    try:
        for model in (
            Organization,
            User,
            Zone,
            Gateway,
            Sensor,
            Plant,
            PlantSensorAssignment,
            PlantObservationAccess,
            PlantObservationSession,
            PlantObservation,
            PlantObservationPhoto,
            PlantEvaluation,
        ):
            model.__table__.create(engine)
        with Session(engine) as db:
            org = Organization(name="Synthetic public access")
            db.add(org)
            db.flush()
            zone = Zone(name="Synthetic", organization_id=org.id)
            user = User(
                email="public-access@example.invalid",
                password_hash="unused",
                role=Role.ADMIN,
                organization_id=org.id,
            )
            db.add_all([zone, user])
            db.flush()
            plant = Plant(name="Synthetic plant", organization_id=org.id, zone_id=zone.id)
            db.add(plant)
            db.commit()
            data = {"plant": plant.id, "user": user.id, "org": org.id, "zone": zone.id}
        yield engine, data
    finally:
        engine.dispose()
        with control.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()


def _issue(engine, data):
    with Session(engine) as db:
        return get_or_create_observation_access(db, db.get(User, data["user"]), data["plant"])


def _session(engine, public_id):
    with Session(engine) as db:
        return observation_service.create_observation_session(
            db, public_id, "127.0.0.1", "synthetic"
        ).session_token


def _revoke(engine, data):
    with Session(engine) as db:
        return revoke_observation_access(db, db.get(User, data["user"]), data["plant"])


def test_concurrent_creation_returns_one_capability(pg_access):
    engine, data = pg_access
    start = threading.Barrier(6)

    def issue(_):
        start.wait(timeout=10)
        return _issue(engine, data).id

    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(issue, range(6)))
    assert len(set(ids)) == 1
    with Session(engine) as db:
        assert db.query(PlantObservationAccess).filter_by(is_active=True).count() == 1


def test_default_creation_and_revocation_leave_no_active_capability(pg_access):
    engine, data = pg_access
    grant = _issue(engine, data)
    token = _session(engine, grant.public_id)
    start = threading.Barrier(2)

    def issue():
        start.wait(timeout=10)
        try:
            _issue(engine, data)
            return 200
        except HTTPException as error:
            return error.status_code

    def revoke():
        start.wait(timeout=10)
        _revoke(engine, data)

    with ThreadPoolExecutor(max_workers=2) as pool:
        issued, revoked = pool.submit(issue), pool.submit(revoke)
        assert issued.result(timeout=10) in (200, 409)
        revoked.result(timeout=10)
    with Session(engine) as db:
        assert db.query(PlantObservationAccess).filter_by(is_active=True).count() == 0
        assert db.query(PlantObservationSession).filter_by(is_active=True).count() == 0
        with pytest.raises(HTTPException) as error:
            observation_service.get_plant_context(db, token)
        assert error.value.status_code == 401


def test_session_issuance_racing_revoke_cannot_escape_cleanup(pg_access):
    engine, data = pg_access
    grant = _issue(engine, data)
    start = threading.Barrier(5)

    def issue(_):
        start.wait(timeout=10)
        try:
            return _session(engine, grant.public_id)
        except HTTPException as error:
            assert error.status_code == 404
            return None

    def revoke():
        start.wait(timeout=10)
        _revoke(engine, data)

    with ThreadPoolExecutor(max_workers=5) as pool:
        pending = [pool.submit(issue, n) for n in range(4)]
        revocation = pool.submit(revoke)
        tokens = [future.result(timeout=10) for future in pending]
        revocation.result(timeout=10)
    with Session(engine) as db:
        assert db.query(PlantObservationSession).filter_by(is_active=True).count() == 0
        for token in filter(None, tokens):
            with pytest.raises(HTTPException):
                observation_service.get_plant_context(db, token)


def test_revoke_waits_for_validated_write_and_blocks_followups(pg_access, monkeypatch):
    engine, data = pg_access
    grant = _issue(engine, data)
    token = _session(engine, grant.public_id)
    validated, release = threading.Event(), threading.Event()
    validate = observation_service._get_valid_session

    def hold(*args):
        result = validate(*args)
        validated.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(observation_service, "_get_valid_session", hold)

    def write():
        with Session(engine) as db:
            return observation_service.create_observation(
                db, token, PlantObservationCreate(wellbeing_score=4, plant_condition="good")
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(write)
        assert validated.wait(10)
        revoked = pool.submit(_revoke, engine, data)
        try:
            # Prove a real lock wait, not just delayed Python scheduling.
            import time

            deadline = time.monotonic() + 10
            waiting = False
            while time.monotonic() < deadline:
                with engine.connect() as connection:
                    waiting = connection.execute(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE application_name = current_setting('application_name') AND wait_event_type = 'Lock')"
                        )
                    ).scalar_one()
                if waiting:
                    break
                time.sleep(0.02)
            assert waiting and not revoked.done()
        finally:
            release.set()
        assert pending.result(timeout=10).plant_id == str(data["plant"])
        revoked.result(timeout=10)
    monkeypatch.setattr(observation_service, "_get_valid_session", validate)
    with Session(engine) as db:
        assert db.query(PlantObservation).count() == 1
        with pytest.raises(HTTPException):
            observation_service.create_observation(
                db, token, PlantObservationCreate(wellbeing_score=4, plant_condition="good")
            )
        assert db.query(PlantObservation).count() == 1


def test_unique_index_rejects_noncooperating_writer(pg_access):
    engine, data = pg_access
    _issue(engine, data)
    with Session(engine) as db:
        db.add(PlantObservationAccess(plant_id=data["plant"]))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


@pytest.mark.parametrize("revision", ["0023", "0024", "0025"])
def test_upgrade_invalidates_all_ambiguous_grants_and_preserves_history(pg_access, revision):
    engine, data = pg_access
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX uq_plant_observation_access_active_plant"))
        connection.execute(
            text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
        )
        connection.execute(
            text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": revision}
        )
    with Session(engine) as db:
        other = Plant(name="Unambiguous", organization_id=data["org"], zone_id=data["zone"])
        db.add(other)
        db.flush()
        grants = [
            PlantObservationAccess(plant_id=data["plant"]),
            PlantObservationAccess(plant_id=data["plant"]),
            PlantObservationAccess(plant_id=data["plant"], is_active=False),
            PlantObservationAccess(plant_id=other.id),
        ]
        db.add_all(grants)
        db.flush()
        for grant in grants:
            db.add(
                PlantObservationSession(
                    plant_id=grant.plant_id,
                    access_id=grant.id,
                    session_token=str(uuid4()),
                    expires_at=datetime.now(UTC) + timedelta(minutes=30),
                )
            )
        db.add(
            PlantObservation(
                plant_id=data["plant"],
                zone_id=data["zone"],
                wellbeing_score=4,
                plant_condition="good",
            )
        )
        db.commit()
        good_id = grants[-1].id
    prepare_observation_access(engine)
    prepare_observation_access(engine)
    assert dict(observation_access_status(engine)) == {False: 3, True: 1}
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == revision
        )
    with Session(engine) as db:
        assert db.query(PlantObservationSession).filter_by(is_active=True).count() == 1
        assert db.get(PlantObservationAccess, good_id).is_active
        assert db.query(PlantObservation).count() == 1
        assert db.query(PlantObservationAccess).count() == 4
        user = db.get(User, data["user"])
        with pytest.raises(HTTPException) as error:
            get_or_create_observation_access(db, user, data["plant"])
        assert error.value.status_code == 409
        fresh = get_or_create_observation_access(db, user, data["plant"], reissue=True)
        assert fresh.is_active
    prepare_observation_access(engine)
    assert dict(observation_access_status(engine)) == {False: 3, True: 2}


@pytest.mark.parametrize(
    "definition",
    [
        "ON plant_observation_access(public_id)",
        "ON plant_observation_access(plant_id) WHERE NOT is_active",
    ],
)
def test_status_rejects_wrong_unique_index(pg_access, definition):
    engine, data = pg_access
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX uq_plant_observation_access_active_plant"))
        connection.execute(
            text("CREATE UNIQUE INDEX uq_plant_observation_access_active_plant " + definition)
        )
    with pytest.raises(ValueError, match="Prepare"):
        observation_access_status(engine)
    prepare_observation_access(engine)
    assert observation_access_status(engine) == []
