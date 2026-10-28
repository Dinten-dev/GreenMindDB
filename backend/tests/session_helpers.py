"""Persist real sessions for authenticated tests; no production auth override."""

from app.auth import create_session_token


def create_access_token(db, data, expires_delta=None):
    token = create_session_token(db, data["sub"], expires_delta)
    db.commit()
    return token
