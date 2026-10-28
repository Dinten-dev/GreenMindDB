"""Readiness checks use operator-supplied, login-issued sessions; never bypass sid."""

import json
import os
from datetime import timedelta
from pathlib import Path

from app.auth import create_access_token, decode_token


def validation_access_token(data, expires_delta: timedelta | None = None):
    path = Path(os.environ.get("GREENMIND_VALIDATION_TOKENS_FILE", ""))
    if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o077:
        raise RuntimeError("A private validation-token file containing real login sessions is required")
    if path.stat().st_size > 65536:
        raise RuntimeError("Validation-token file exceeds bound")
    tokens = json.loads(path.read_text())
    token = tokens.get(str(data["sub"]))
    payload = decode_token(token) if isinstance(token, str) else None
    if not payload or payload["sub"] != str(data["sub"]):
        raise RuntimeError("No valid login-issued session supplied for this validation user")
    if expires_delta is not None and expires_delta.total_seconds() < 0:
        return create_access_token({"sub": payload["sub"], "sid": payload["sid"]}, expires_delta)
    return token
