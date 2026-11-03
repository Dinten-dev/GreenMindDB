"""Private Unix-socket single-owner preview worker. No database or object store."""

import asyncio
import fcntl
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.direct.live_state import LiveStore
from app.direct.protocol import DirectError


def create_live_worker(store=None, socket_path=None):
    live = store or LiveStore()

    async def expire_idle_state():
        # Expire RAM even when every device/viewer has disconnected.
        while True:
            await asyncio.sleep(1)
            with live.lock:
                live._prune()

    @asynccontextmanager
    async def lifespan(_):
        handle = None
        if socket_path is not None:
            directory = Path(socket_path).parent
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            stat = directory.stat()
            if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
                raise RuntimeError("Live socket directory must be privately owned (0700)")
            fd = os.open(directory / "owner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            handle = os.fdopen(fd, "a")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                raise RuntimeError("A live-state owner already exists") from None
        expiry = asyncio.create_task(expire_idle_state())
        try:
            yield
        finally:
            expiry.cancel()
            with suppress(asyncio.CancelledError):
                await expiry
            if handle:
                handle.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.live = live

    @app.exception_handler(DirectError)
    async def domain_error(_, error):
        return JSONResponse({"error": error.code}, status_code=error.status)

    @app.get("/health")
    def health():
        return {"live": "volatile", "history_seconds": 180, "lease_seconds": 30}

    @app.post("/dispatch")
    async def dispatch(request: Request):
        from app.direct.live_api import bounded_json

        body = await bounded_json(request, 8192)
        try:
            return app.state.live.dispatch(body)
        except (ValidationError, ValueError, KeyError, TypeError):
            raise DirectError(422, "invalid_live_request") from None

    return app


def main():
    import uvicorn

    path = Path(os.getenv("DIRECT_LIVE_SOCKET", "/run/direct-live/live.sock"))
    if not path.is_absolute():
        raise ValueError("Live socket must be absolute")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stat = path.parent.stat()
    if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise RuntimeError("Live socket directory must be privately owned (0700)")
    fd = os.open(path.parent / "owner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as owner:
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("A live-state owner already exists") from None
        if path.exists() and not path.is_socket():
            raise RuntimeError("Live socket path contains a non-socket file")
        os.umask(0o077)
        uvicorn.run(
            create_live_worker(),
            uds=str(path),
            workers=1,
            limit_concurrency=64,
            timeout_keep_alive=2,
            access_log=False,
        )


if __name__ == "__main__":
    main()
