"""Operational failures must be distinguishable without exposing driver data."""

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("55P03", "lock_timeout"),
        ("08006", "connection_failure"),
        ("secret-driver-text", "unclassified"),
    ],
)
def test_database_error_is_classified_without_sql_or_credentials(
    pipeline, monkeypatch, caplog, code, expected
):
    from app.direct import main

    def unavailable(*args, **kwargs):
        raise OperationalError(
            "SECRET SQL",
            {"token": "SECRET TOKEN"},
            SimpleNamespace(pgcode=code, message="SECRET PAYLOAD"),
        )

    monkeypatch.setattr(main, "accept_chunk", unavailable)
    response = pipeline.upload(bytes(1200))
    assert response.status_code == 503
    assert response.json() == {"error": "direct_database_unavailable"}
    assert response.headers["retry-after"] == "30"
    assert f"category={expected}" in caplog.text
    assert "SECRET" not in caplog.text
    assert "secret-driver-text" not in caplog.text


def test_readiness_progress_tracks_commits_without_scanning_chunk_history(pipeline):
    import time

    from sqlalchemy import event, func

    from app.direct.database import transaction
    from app.direct.models import Chunk, Heartbeat

    p = pipeline
    payload = bytes(1200)
    for i in range(12):
        assert (
            p.upload(payload, p.metadata(payload, sequence=i, first_frame=i * 100)).status_code
            == 201
        )
    with transaction(p.engine) as db:
        expected = db.query(func.max(Chunk.received_at)).scalar()
        db.add(Heartbeat(name="assembler", status="healthy", updated_at=time.time()))
    # A retry is acknowledged, but must not fabricate newer persisted progress.
    assert p.upload(payload).status_code == 200
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(p.engine, "before_cursor_execute", capture)
    try:
        response = p.client.get("/readiness")
    finally:
        event.remove(p.engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert response.json()["last_persisted_at"] == expected
    assert not any("FROM direct_chunk" in statement for statement in statements)
