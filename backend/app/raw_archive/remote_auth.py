"""Authenticate through the original application, without exporting its JWT key."""

import json
import os
from uuid import UUID

import httpx

from .policy import ArchiveBlocked

ORIGIN = "http://127.0.0.1:18120/api/v1/auth/me"


def authenticate(data, *, client_factory=httpx.Client):
    if os.getenv("RAW_ARCHIVE_AUTH_SOURCE_URL") != ORIGIN:
        raise ArchiveBlocked("Only the existing private source-auth tunnel is permitted")
    if not isinstance(data, dict) or set(data) != {"authorization", "access_token"}:
        raise ArchiveBlocked("Exact authentication fields required")
    for value in data.values():
        if value is not None and (
            not isinstance(value, str)
            or len(value) > 4096
            or any(ord(c) < 32 or ord(c) > 126 for c in value)
        ):
            raise ArchiveBlocked("Invalid bounded authentication input")
    headers = {"X-Forwarded-Proto": "https"}
    if data["authorization"]:
        headers["Authorization"] = data["authorization"]
    if data["access_token"]:
        if ";" in data["access_token"]:
            raise ArchiveBlocked("Invalid access cookie")
        headers["Cookie"] = "access_token=" + data["access_token"]
    if len(headers) == 1:
        return 401, None
    with client_factory(
        timeout=httpx.Timeout(3, connect=1), trust_env=False, follow_redirects=False
    ) as client:
        with client.stream("GET", ORIGIN, headers=headers) as response:
            if response.status_code in (401, 403):
                return response.status_code, None
            if response.status_code != 200:
                raise ArchiveBlocked("Source authentication unavailable")
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 8192:
                    raise ArchiveBlocked("Source authentication response exceeds bound")
    result = json.loads(raw)
    if not isinstance(result, dict) or not isinstance(result.get("id"), str):
        raise ArchiveBlocked("Source actor identity missing")
    # Only the authenticated identity crosses the private broker. Never forward
    # source profile details, email, credentials or an unverified token claim.
    return 200, {"id": str(UUID(result["id"]))}
