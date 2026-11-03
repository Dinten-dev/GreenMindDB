"""Run the raw profile/WAV contract against real PostgreSQL transactions."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.direct.database import transaction
from app.direct.models import Budget, Chunk, Session
from tests.direct import test_raw400_profile as contract

pytest_plugins = ("tests.direct.test_postgres",)
pytestmark = pytest.mark.integration


def test_raw400_profile_wav_rails_and_replay_on_postgres(pg_pipeline):
    contract.test_raw400_upload_retry_wav_and_millivolt_scaling_keep_both_rails(pg_pipeline)


def test_raw400_parallel_retries_commit_one_payload_and_profile(pg_pipeline):
    p = pg_pipeline
    payload = b"\xff\x7f" * 400
    meta = contract.raw_meta(p, payload)
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda _: p.upload(payload, meta).status_code, range(20)))
    assert sorted(codes) == [200] * 19 + [201]
    with transaction(p.engine) as db:
        assert db.query(Chunk).count() == 1
        assert db.query(Session).one().config["signal_profile"] == "ad8232-raw400-v1"
        assert db.get(Budget, 1).used_bytes == 800
