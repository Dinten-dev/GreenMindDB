"""Bound bytes before FastAPI parses JSON or multipart request bodies."""

import re

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

JSON_BYTES = 64 * 1024
INGEST_BYTES = 16 * 1024 * 1024
MULTIPART_OVERHEAD = 64 * 1024
PHOTO_BYTES = 10 * 1024 * 1024
FIRMWARE_BYTES = 16 * 1024 * 1024
GATEWAY_RELEASE_BYTES = 100 * 1024 * 1024
_PHOTO_PATH = re.compile(r"^/api/v1/public/observe/session/[^/]+/observations/[^/]+/photos$")


class RequestBodyLimitMiddleware:
    def __init__(self, app, *, wav_bytes=32 * 1024 * 1024):
        self.app = app
        self.wav_bytes = wav_bytes

    def policy(self, path):
        path = path.rstrip("/")
        if path == "/api/v1/wav/upload":
            return self.wav_bytes + MULTIPART_OVERHEAD, True
        if path == "/api/v1/firmware/upload":
            return FIRMWARE_BYTES + MULTIPART_OVERHEAD, True
        if path == "/api/v1/admin/gateway-app-releases":
            return GATEWAY_RELEASE_BYTES + MULTIPART_OVERHEAD, True
        if _PHOTO_PATH.fullmatch(path):
            return PHOTO_BYTES + MULTIPART_OVERHEAD, True
        return (INGEST_BYTES if path == "/api/v1/ingest" else JSON_BYTES), False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit, streaming = self.policy(scope.get("path", ""))
        lengths = [
            value for name, value in scope.get("headers", []) if name.lower() == b"content-length"
        ]
        if lengths and (len(lengths) != 1 or not re.fullmatch(rb"[0-9]{1,20}", lengths[0])):
            return await JSONResponse({"detail": "Invalid Content-Length"}, 400)(
                scope, receive, send
            )
        if lengths and int(lengths[0]) > limit:
            return await JSONResponse({"detail": "Request body exceeds limit"}, 413)(
                scope, receive, send
            )
        encodings = [
            value.lower()
            for name, value in scope.get("headers", [])
            if name.lower() == b"content-encoding"
        ]
        if any(value != b"identity" for value in encodings):
            return await JSONResponse({"detail": "Content-Encoding is not supported"}, 415)(
                scope, receive, send
            )

        size = 0

        async def bounded_receive():
            nonlocal size
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > limit:
                    raise HTTPException(413, "Request body exceeds limit")
            return message

        if streaming:
            # Multipart remains spooled/streamed by Starlette. HTTPException is
            # deliberately preserved by FastAPI's body parser as status 413.
            return await self.app(scope, bounded_receive, send)

        # JSON must be bounded *before* any parser, dependency or endpoint rate
        # decorator runs. Never append a chunk that crosses the byte limit.
        body = bytearray()
        try:
            while True:
                message = await bounded_receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    break
        except HTTPException:
            return await JSONResponse({"detail": "Request body exceeds limit"}, 413)(
                scope, receive, send
            )

        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)
