"""Run the additive migration in an isolated schema of a disposable local DB."""

import importlib.util
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.master import Zone
from app.models.user import EmailVerification, Organization, Role, User
from app.routers.organizations import update_member_access
from app.schemas.organization import UpdateMemberZones
from app.zone_access import allowed_zone_ids


@pytest.mark.integration
def test_additive_zone_grants_preserve_existing_members_and_deny_new_implicit_access():
    url = os.environ.get("VISUAL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Disposable local PostgreSQL required")
    parsed = make_url(url)
    assert parsed.host in ("127.0.0.1", "localhost") and parsed.database == "visual_test"
    engine = create_engine(url)
    schema = "zone_access_test_" + uuid4().hex
    script = Path(__file__).parents[1] / "alembic/versions/0023_zone_access.py"
    spec = importlib.util.spec_from_file_location("zone_migration", script)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}",public'))
            connection = connection.execution_options(schema_translate_map={None: schema})
            for table in (Organization.__table__, User.__table__, Zone.__table__):
                table.create(connection)
            org, other = uuid4(), uuid4()
            member, admin, foreign = uuid4(), uuid4(), uuid4()
            zones = [uuid4(), uuid4(), uuid4()]
            connection.execute(
                Organization.__table__.insert(),
                [{"id": org, "name": "A"}, {"id": other, "name": "B"}],
            )
            for identity, role, tenant in (
                (member, Role.MEMBER, org),
                (admin, Role.ADMIN, org),
                (foreign, Role.MEMBER, other),
            ):
                connection.execute(
                    User.__table__.insert().values(
                        id=identity,
                        email=str(identity) + "@example.com",
                        password_hash="unused",
                        role=role,
                        organization_id=tenant,
                    )
                )
            for identity, tenant in ((zones[0], org), (zones[1], org), (zones[2], other)):
                connection.execute(
                    Zone.__table__.insert().values(id=identity, name="Zone", organization_id=tenant)
                )
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            grants = set(connection.execute(text("SELECT user_id,zone_id FROM zone_access")).all())
            assert grants == {(member, zones[0]), (member, zones[1]), (foreign, zones[2])}
            new_member = uuid4()
            connection.execute(
                User.__table__.insert().values(
                    id=new_member,
                    email=str(new_member) + "@example.com",
                    password_hash="unused",
                    role=Role.MEMBER,
                    organization_id=org,
                )
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM zone_access WHERE user_id=:id"), {"id": new_member}
                ).scalar_one()
                == 0
            )
            # Exercise the real update path against PostgreSQL, including User's
            # joined organization relationship and its explicitly scoped row lock.
            AuditLog.__table__.create(connection)
            with Session(bind=connection) as session:
                manager = session.get(User, admin)
                target = session.get(User, new_member)
                response = update_member_access(
                    new_member, UpdateMemberZones(zone_ids=zones[:2]), manager, session
                )
                assert set(response.zone_ids) == set(map(str, zones[:2]))
                assert set(allowed_zone_ids(session, target)) == set(zones[:2])
                update_member_access(new_member, UpdateMemberZones(zone_ids=[]), manager, session)
                assert allowed_zone_ids(session, target) == []
                assert session.query(AuditLog).filter_by(action="zone_access.update").count() == 2
            connection.execute(text("DELETE FROM zone WHERE id=:id"), {"id": zones[0]})
            assert (
                connection.execute(
                    text("SELECT count(*) FROM zone_access WHERE zone_id=:id"), {"id": zones[0]}
                ).scalar_one()
                == 0
            )
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


@pytest.mark.integration
def test_management_user_creation_and_company_change_on_postgres(monkeypatch):
    """Exercise real FK ordering, joined-user row locks, grants and company refresh."""
    from app.config import settings
    from app.models.zone_access import ZoneAccess
    from app.routers.administration import create_user, delete_user, update_user
    from app.schemas.administration import AdminUserCreate, AdminUserDelete, AdminUserUpdate

    url = os.environ.get("VISUAL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Disposable local PostgreSQL required")
    parsed = make_url(url)
    assert parsed.host in ("127.0.0.1", "localhost") and parsed.database == "visual_test"
    monkeypatch.setattr(settings, "management_admin_emails", "operator@example.com")
    engine = create_engine(url)
    schema = "management_test_" + uuid4().hex
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}",public'))
            connection = connection.execution_options(schema_translate_map={None: schema})
            for table in (
                Organization.__table__,
                User.__table__,
                Zone.__table__,
                ZoneAccess.__table__,
                AuditLog.__table__,
                EmailVerification.__table__,
            ):
                table.create(connection)
            with Session(bind=connection) as session:
                first, second = Organization(name="A"), Organization(name="B")
                session.add_all([first, second])
                session.flush()
                actor = User(
                    email="operator@example.com",
                    password_hash="disabled",
                    role=Role.ADMIN,
                    organization_id=first.id,
                    is_active=True,
                    is_verified=True,
                )
                zone = Zone(name="B zone", organization_id=second.id)
                session.add_all([actor, zone])
                session.flush()
                created = create_user(
                    AdminUserCreate(
                        email="pg.member@example.com",
                        name="PG member",
                        password="TestLongPassword12",
                        organization_id=first.id,
                    ),
                    actor,
                    session,
                )
                changed = update_user(
                    UUID(created["id"]),
                    AdminUserUpdate(
                        name="PG moved",
                        organization_id=second.id,
                        role=Role.MEMBER,
                        is_active=True,
                        zone_ids=[zone.id],
                    ),
                    actor,
                    session,
                )
                assert changed["organization_name"] == "B"
                assert changed["zone_ids"] == [str(zone.id)]
                assert session.query(ZoneAccess).count() == 1
                assert session.query(AuditLog).count() == 2
                target_id = UUID(created["id"])
                session.add(AuditLog(user_id=target_id, action="history.keep", entity_type="user"))
                session.add(
                    EmailVerification(
                        user_id=target_id, token="test-token", expires_at=datetime.now(UTC)
                    )
                )
                session.commit()
                assert changed["visible_zones"] == [{"id": str(zone.id), "name": zone.name}]
                assert (
                    delete_user(
                        target_id,
                        AdminUserDelete(confirmation_email=created["email"]),
                        actor,
                        session,
                    ).status_code
                    == 204
                )
                assert session.get(User, target_id) is None
                assert session.query(ZoneAccess).count() == 0
                assert session.query(EmailVerification).count() == 0
                assert (
                    session.query(AuditLog).filter_by(action="history.keep").one().user_id is None
                )
                assert session.get(Zone, zone.id) is not None
                assert session.get(Organization, second.id) is not None
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
