"""A queued authentication query must not block completion of another request."""

import asyncio
import threading
import uuid
from types import SimpleNamespace

import httpx
from fastapi import Depends, FastAPI

from app.auth import create_access_token, get_current_user
from app.database import get_db


def test_two_authenticated_requests_finish_with_one_database_connection():
    identity = uuid.uuid4()
    user = SimpleNamespace(id=identity, is_active=True, is_verified=True)
    available = threading.Semaphore(1)
    acquired = []

    class LimitedSession:
        held = False

        def query(self, *_):
            return self

        def filter(self, *_):
            return self

        def first(self):
            if not available.acquire(timeout=0.5):
                raise TimeoutError("Authentication blocked the event loop")
            self.held = True
            acquired.append(1)
            return user

        def close(self):
            if self.held:
                self.held = False
                available.release()

    def session():
        value = LimitedSession()
        try:
            yield value
        finally:
            value.close()

    app = FastAPI()
    app.dependency_overrides[get_db] = session

    @app.get("/private")
    async def private(current=Depends(get_current_user)):
        # The first response needs the event loop before releasing its DB slot.
        await asyncio.sleep(0.02)
        return {"id": str(current.id)}

    async def exercise():
        token = create_access_token({"sub": str(identity)})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await asyncio.gather(
                *[
                    client.get("/private", headers={"Authorization": "Bearer " + token})
                    for _ in range(2)
                ]
            )

    responses = asyncio.run(exercise())
    assert [response.status_code for response in responses] == [200, 200]
    assert [response.json() for response in responses] == [{"id": str(identity)}] * 2
    assert len(acquired) == 2
