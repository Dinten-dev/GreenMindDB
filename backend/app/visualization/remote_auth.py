"""Optional archive-host authentication: original source remains authoritative."""

import asyncio
import os
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.raw_archive.broker_client import authenticated_actor
from app.raw_archive.policy import ArchiveBlocked

AUTH_SLOTS = asyncio.Semaphore(2)


async def source_user(request: Request, db: Session = Depends(get_db)):
    try:
        async with AUTH_SLOTS:
            status, actor = await run_in_threadpool(
                authenticated_actor,
                request.headers.get("authorization"),
                request.cookies.get("access_token"),
            )
        if status == 200 and not isinstance(actor, str):
            raise ArchiveBlocked("Invalid authenticated actor")
    except (ArchiveBlocked, OSError, ValueError, TypeError):
        raise HTTPException(503, "Source authentication unavailable") from None
    if status != 200:
        raise HTTPException(status, "Not authenticated")
    # Current database state remains required for all tenant/zone decisions.
    # Source already checked signature, expiry, active and verified status.
    user = await run_in_threadpool(db.get, User, UUID(actor))
    if user is None:
        raise HTTPException(401, "User not found")
    if not user.is_active or not user.is_verified:
        raise HTTPException(403, "Account unavailable")
    return user


def install(app):
    if os.getenv("ARCHIVE_REMOTE_AUTH_ENABLED", "false") != "true":
        return
    if not os.getenv("RAW_ARCHIVE_READ_BROKER_SOCKET"):
        raise ArchiveBlocked("Private authentication broker required")
    app.dependency_overrides[get_current_user] = source_user
