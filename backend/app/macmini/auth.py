from typing import Optional

from fastapi import Header, HTTPException, status

from app.macmini.config import get_settings

settings = get_settings()


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return None
    return authorization[len(prefix):].strip()


def require_ingest_auth(authorization: Optional[str] = Header(default=None)) -> None:
    token = _extract_bearer_token(authorization)
    if token != settings.ingest_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing ingest token",
        )


def require_read_auth(authorization: Optional[str] = Header(default=None)) -> None:
    if not settings.read_token_required:
        return

    token = _extract_bearer_token(authorization)
    if token != settings.effective_read_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing read token",
        )
