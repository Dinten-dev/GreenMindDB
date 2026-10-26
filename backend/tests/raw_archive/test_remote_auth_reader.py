"""Archive readers still require original authentication and current users."""

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from app.auth import get_current_user
from app.raw_archive.policy import ArchiveBlocked
from app.visualization import remote_auth


def request():
    return Request({"type": "http", "headers": [(b"authorization", b"Bearer fixture")]})


def test_optional_install_disabled_and_private_broker_required(monkeypatch):
    app = FastAPI()
    monkeypatch.delenv("ARCHIVE_REMOTE_AUTH_ENABLED", raising=False)
    remote_auth.install(app)
    assert not app.dependency_overrides
    monkeypatch.setenv("ARCHIVE_REMOTE_AUTH_ENABLED", "true")
    monkeypatch.delenv("RAW_ARCHIVE_READ_BROKER_SOCKET", raising=False)
    with pytest.raises(ArchiveBlocked):
        remote_auth.install(app)
    monkeypatch.setenv("RAW_ARCHIVE_READ_BROKER_SOCKET", "/private/read.sock")
    remote_auth.install(app)
    assert app.dependency_overrides == {get_current_user: remote_auth.source_user}


@pytest.mark.parametrize("status,expected", [(401, 401), (403, 403), (200, 401)])
def test_current_user_required_even_with_valid_source_actor(monkeypatch, status, expected):
    actor = str(uuid4())
    monkeypatch.setattr(remote_auth, "authenticated_actor", lambda *args: (status, actor))
    db = SimpleNamespace(get=lambda *args: None)
    with pytest.raises(HTTPException) as error:
        asyncio.run(remote_auth.source_user(request(), db))
    assert error.value.status_code == expected


def test_source_actor_uses_current_database_user(monkeypatch):
    actor = str(uuid4())
    user = SimpleNamespace(is_active=True, is_verified=True)
    looked_up = []

    def lookup(model, identity):
        looked_up.append(identity)
        return user

    monkeypatch.setattr(remote_auth, "authenticated_actor", lambda *args: (200, actor))
    assert asyncio.run(remote_auth.source_user(request(), SimpleNamespace(get=lookup))) is user
    assert looked_up == [UUID(actor)]
    user.is_active = False
    with pytest.raises(HTTPException) as error:
        asyncio.run(remote_auth.source_user(request(), SimpleNamespace(get=lookup)))
    assert error.value.status_code == 403


def test_no_trust_in_malformed_actor(monkeypatch):
    monkeypatch.setattr(remote_auth, "authenticated_actor", lambda *args: (200, None))
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            remote_auth.source_user(
                request(), SimpleNamespace(get=lambda *args: pytest.fail("No SQL"))
            )
        )
    assert error.value.status_code == 503


def test_two_auth_requests_maximum(monkeypatch):
    actor = str(uuid4())
    active = maximum = 0

    async def check(*args):
        nonlocal active, maximum
        if args[0] is not remote_auth.authenticated_actor:
            return args[0](*args[1:])
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return 200, actor

    monkeypatch.setattr(remote_auth, "run_in_threadpool", check)

    async def batch():
        monkeypatch.setattr(remote_auth, "AUTH_SLOTS", asyncio.Semaphore(2))
        user = SimpleNamespace(is_active=True, is_verified=True)
        db = SimpleNamespace(get=lambda *args: user)
        await asyncio.gather(*(remote_auth.source_user(request(), db) for _ in range(12)))

    asyncio.run(batch())
    assert maximum == 2


def test_database_wait_does_not_block_request_completion(tmp_path, monkeypatch):
    from sqlalchemy import Boolean, Uuid, create_engine
    from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
    from sqlalchemy.pool import QueuePool

    class Base(DeclarativeBase):
        pass

    class TestUser(Base):
        __tablename__ = "users"
        id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
        is_active: Mapped[bool] = mapped_column(Boolean)
        is_verified: Mapped[bool] = mapped_column(Boolean)

    engine = create_engine(
        "sqlite:///" + str(tmp_path / "auth.sqlite"),
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.25,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    actor = uuid4()
    with Session(engine) as db:
        db.add(TestUser(id=actor, is_active=True, is_verified=True))
        db.commit()
    monkeypatch.setattr(remote_auth, "User", TestUser)
    monkeypatch.setattr(remote_auth, "authenticated_actor", lambda *args: (200, str(actor)))

    async def batch():
        monkeypatch.setattr(remote_auth, "AUTH_SLOTS", asyncio.Semaphore(2))
        occupied = asyncio.Event()

        async def first():
            with Session(engine) as db:
                user = await remote_auth.source_user(request(), db)
                occupied.set()
                await asyncio.sleep(0.025)
                return user.id

        async def second():
            await occupied.wait()
            with Session(engine) as db:
                return (await remote_auth.source_user(request(), db)).id

        return await asyncio.gather(first(), second())

    try:
        assert asyncio.run(batch()) == [actor, actor]
    finally:
        engine.dispose()
