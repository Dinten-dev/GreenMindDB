"""Missing historical parents must not consume valid sensors' projection batches."""

import hashlib
import io
import os
import struct
import uuid
import wave
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.services import wav_service
from app.visualization import worker

pytestmark = pytest.mark.integration


@pytest.fixture
def waves(monkeypatch):
    url = os.getenv("VISUAL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable local visual_test PostgreSQL")
    parsed = make_url(url)
    assert parsed.host in ("localhost", "127.0.0.1") and parsed.database == "visual_test"
    schema = "wave_parent_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as db:
        db.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(url, connect_args={"options": f"-c search_path={schema}"})
    with engine.begin() as db:
        db.execute(
            text("""CREATE TABLE sensor(id uuid PRIMARY KEY);
        CREATE TABLE wav_file(id uuid PRIMARY KEY,sensor_id uuid,started_at timestamptz,
          ended_at timestamptz,feature_status text,raw_deleted_at timestamptz,
          timing_status text,coverage_ratio double precision,s3_key text,sample_rate integer,
          pcm_scale_mv double precision,pcm_offset_mv double precision);
        CREATE TABLE wav_feature(wav_file_id uuid PRIMARY KEY,source_sha256 text);
        CREATE TABLE visual_wav(wav_id uuid PRIMARY KEY REFERENCES wav_file(id),
          source_sha256 text,seconds integer,status text,error text,updated_at timestamptz DEFAULT now());
        CREATE TABLE visual_wave(wav_id uuid REFERENCES visual_wav(wav_id),
          sensor_id uuid REFERENCES sensor(id),bucket timestamptz,seconds integer,n bigint,
          total double precision,total2 double precision,minimum double precision,
          maximum double precision,duration double precision);""")
        )
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, 380, 380, "NONE", "not compressed"))
        wav.writeframes(struct.pack("<380h", *range(380)))
    payload = output.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    now = datetime.now(UTC)
    calls = []

    def get_object(*, Bucket, Key):
        calls.append(Key)
        return {"ContentLength": len(payload), "Body": io.BytesIO(payload)}

    monkeypatch.setattr(
        wav_service, "_get_s3_client", lambda: SimpleNamespace(get_object=get_object)
    )

    def add(*, parent=True, age=60):
        sid, wid = uuid.uuid4(), uuid.uuid4()
        with engine.begin() as db:
            if parent:
                db.execute(text("INSERT INTO sensor VALUES (:sid)"), {"sid": sid})
            db.execute(
                text("""INSERT INTO wav_file VALUES (:wid,:sid,:start,:end,'verified',NULL,
              'complete',1,:key,380,1,0);
              INSERT INTO wav_feature VALUES (:wid,:sha);"""),
                {
                    "wid": wid,
                    "sid": sid,
                    "start": now - timedelta(seconds=age + 1),
                    "end": now - timedelta(seconds=age),
                    "key": str(wid) + ".wav",
                    "sha": digest,
                },
            )
        return sid, wid

    yield SimpleNamespace(engine=engine, now=now, calls=calls, add=add, payload=payload)
    engine.dispose()
    with admin.begin() as db:
        db.execute(text(f"DROP SCHEMA {schema} CASCADE"))
    admin.dispose()


def test_orphan_wavs_do_not_fill_limit_or_download_before_valid_wav(waves):
    p = waves
    _, valid = p.add(age=90)
    p.add(parent=False, age=10)
    p.add(parent=False, age=20)
    candidates = worker.wave_candidates(p.engine, p.now, limit=1)
    assert [x["id"] for x in candidates] == [valid]
    worker.process_wav(p.engine, candidates[0], p.now)
    assert p.calls == [str(valid) + ".wav"]
    with p.engine.connect() as db:
        assert (
            db.execute(
                text("SELECT count(*) FROM wav_file WHERE raw_deleted_at IS NULL")
            ).scalar_one()
            == 3
        )
        assert db.execute(text("SELECT count(*) FROM wav_feature")).scalar_one() == 3
        assert (
            db.execute(
                text("SELECT status FROM visual_wav WHERE wav_id=:id"), {"id": valid}
            ).scalar_one()
            == "verified"
        )
        assert db.execute(text("SELECT sum(n) FROM visual_wave")).scalar_one() == 380


def test_restored_parent_automatically_reenables_preserved_wav(waves):
    p = waves
    sid, wid = p.add(parent=False)
    assert worker.wave_candidates(p.engine, p.now) == [] and p.calls == []
    with p.engine.begin() as db:
        assert db.execute(text("SELECT count(*) FROM wav_file")).scalar_one() == 1
        assert db.execute(text("SELECT count(*) FROM visual_wav")).scalar_one() == 0
        db.execute(text("INSERT INTO sensor VALUES (:sid)"), {"sid": sid})
    candidates = worker.wave_candidates(p.engine, p.now)
    assert [x["id"] for x in candidates] == [wid]
    worker.process_wav(p.engine, candidates[0], p.now)
    with p.engine.connect() as db:
        assert db.execute(text("SELECT status FROM visual_wav")).scalar_one() == "verified"
        assert db.execute(text("SELECT raw_deleted_at FROM wav_file")).scalar_one() is None
    assert p.calls == [str(wid) + ".wav"]


def test_parent_deleted_after_selection_still_fails_without_false_verification(waves):
    p = waves
    sid, wid = p.add()
    candidate = worker.wave_candidates(p.engine, p.now)[0]
    with p.engine.begin() as db:
        db.execute(text("DELETE FROM sensor WHERE id=:sid"), {"sid": sid})
    with pytest.raises(IntegrityError):
        worker.process_wav(p.engine, candidate, p.now)
    with p.engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM visual_wav")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM visual_wave")).scalar_one() == 0
        assert (
            db.execute(
                text("SELECT raw_deleted_at FROM wav_file WHERE id=:id"), {"id": wid}
            ).scalar_one()
            is None
        )
