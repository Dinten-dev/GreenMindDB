"""Real row-lock/transaction tests, run by CI with a disposable PostgreSQL DB."""

import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.direct.assembler import assemble_one
from app.direct.config import DirectSettings
from app.direct.database import make_engine, transaction
from app.direct.models import Base, Budget, Chunk, Revision
from tests.direct.conftest import build_pipeline

pytestmark = pytest.mark.integration


@pytest.fixture
def pg_pipeline(tmp_path):
    url = os.environ.get("DIRECT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("DIRECT_TEST_POSTGRES_URL not configured")
    parsed = make_url(url)
    assert parsed.host in {"127.0.0.1", "localhost"} and parsed.database == "direct_test"
    cfg = DirectSettings(
        database_url=url,
        ingest_enabled=True,
        require_tls=False,
        artifact_root=tmp_path / "artifacts",
        idle_seconds=1,
    )
    engine = make_engine(cfg)
    Base.metadata.drop_all(engine)
    engine.dispose()
    p = build_pipeline(cfg)
    yield p
    p.client.close()
    Base.metadata.drop_all(p.engine)
    p.engine.dispose()


def test_postgres_same_identity_is_atomic_under_concurrent_http_uploads(pg_pipeline):
    p = pg_pipeline
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: p.upload(bytes(1200)), range(30)))
    assert sorted(response.status_code for response in results) == [200] * 29 + [201]
    with transaction(p.engine) as db:
        assert db.query(Chunk).count() == 1
        assert db.get(Budget, 1).used_bytes == 1200


def test_postgres_two_workers_publish_one_revision(pg_pipeline):
    p = pg_pipeline
    assert p.upload(bytes(1200)).status_code == 201
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2), range(2)
            )
        )
    assert sorted(results) == [False, True]
    with transaction(p.engine) as db:
        assert db.query(Revision).count() == 1


def test_postgres_devices_and_sessions_remain_independent(pg_pipeline):
    p = pg_pipeline
    other = build_pipeline(p.cfg)
    try:
        payload = bytes(1200)

        def upload(item):
            source, sequence = item
            return source.upload(
                payload, source.metadata(payload, sequence=sequence, first_frame=sequence * 100)
            )

        work = [(source, sequence) for sequence in range(20) for source in (p, other)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(upload, work))
        assert all(response.status_code == 201 for response in responses)
        with transaction(p.engine) as db:
            assert db.query(Chunk).filter_by(device_id=p.device_id).count() == 20
            assert db.query(Chunk).filter_by(device_id=other.device_id).count() == 20
            assert db.get(Budget, 1).used_bytes == 48000
    finally:
        other.client.close()
        other.engine.dispose()


def test_postgres_ack_survives_database_restart(pg_pipeline):
    container = os.environ.get("DIRECT_TEST_POSTGRES_CONTAINER")
    if not container:
        pytest.skip("Optional disposable local container restart test")
    assert container == "gm-direct-review-20260912"
    p = pg_pipeline
    payload = bytes(1200)
    assert p.upload(payload).status_code == 201
    p.engine.dispose()
    docker = shutil.which("docker")
    assert docker
    subprocess.run([docker, "restart", container], check=True, capture_output=True, timeout=45)
    for attempt in range(20):
        try:
            with transaction(p.engine) as db:
                db.execute(text("SELECT 1"))
            break
        except OperationalError:
            if attempt == 19:
                raise
            time.sleep(0.25)
    assert p.upload(payload).json()["status"] == "duplicate"
    with transaction(p.engine) as db:
        assert db.query(Chunk).one().payload == payload


def test_hotspot_pairing_code_is_consumed_atomically(pg_pipeline):
    import hashlib
    import uuid

    from app.direct.auth import issue_token
    from app.direct.models import Enrollment, Pairing

    p = pg_pipeline
    p.cfg.dashboard_api_url = "http://unused:8000/api/v1"
    p.cfg.dashboard_origin = "https://test.green-mind.ch"
    code = "ABCD2345"
    with transaction(p.engine) as db:
        db.add(
            Pairing(
                code_hash=hashlib.sha256(code.encode()).hexdigest(),
                organization_id=str(uuid.uuid4()),
                zone_id=str(uuid.uuid4()),
                expires_at=time.time() + 600,
            )
        )
    bodies = []
    for i in range(2):
        device_id = str(uuid.uuid4())
        bodies.append(
            {
                "code": code,
                "device_id": device_id,
                "token": issue_token(device_id)[0],
                "hardware_id": f"14:c1:9f:d9:42:9{i}",
            }
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda body: p.client.post("/api/v1/direct-ingest/register", json=body), bodies
            )
        )
    assert sorted(r.status_code for r in results) == [201, 409]
    winner = bodies[next(i for i, r in enumerate(results) if r.status_code == 201)]
    assert p.client.post("/api/v1/direct-ingest/register", json=winner).status_code == 201
    with transaction(p.engine) as db:
        assert db.query(Enrollment).count() == 1
