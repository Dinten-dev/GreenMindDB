"""Real SELECT semantics in disposable TimescaleDB, never a live source."""

import math
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.visualization.feature_series import series

pytestmark = pytest.mark.integration


def test_sample_weighted_features_hash_gate_and_overlap(request):
    engine, ids, start, add = request.getfixturevalue("postgres_fixture")
    add(start, 1)
    with engine.begin() as db:
        db.execute(
            text("""ALTER TABLE wav_file ADD COLUMN IF NOT EXISTS content_sha256 text;
        ALTER TABLE wav_file ADD COLUMN IF NOT EXISTS calibration_version text;
        ALTER TABLE wav_feature ADD COLUMN IF NOT EXISTS sample_count bigint;
        ALTER TABLE wav_feature ADD COLUMN IF NOT EXISTS sample_rate integer;
        ALTER TABLE wav_feature ADD COLUMN IF NOT EXISTS duration_seconds double precision;
        ALTER TABLE wav_feature ADD COLUMN IF NOT EXISTS mean double precision;
        ALTER TABLE wav_feature ADD COLUMN IF NOT EXISTS rms double precision;
        ALTER TABLE wav_feature ADD COLUMN IF NOT EXISTS minimum double precision;
        ALTER TABLE wav_feature ADD COLUMN IF NOT EXISTS maximum double precision;
        ALTER TABLE wav_feature ADD COLUMN IF NOT EXISTS value_unit text;
        ALTER TABLE wav_feature ADD COLUMN IF NOT EXISTS verified_at timestamptz;
        ALTER TABLE wav_feature ADD COLUMN IF NOT EXISTS calibration_version text;
        ALTER TABLE wav_feature ADD COLUMN IF NOT EXISTS coverage_ratio double precision;
        ALTER TABLE wav_feature ADD COLUMN IF NOT EXISTS timing_status text""")
        )
        for _number, offset, duration, mean, valid in [
            (1, 0, 600, 10, True),
            (2, 599, 300, 20, True),
            (3, 1200, 300, 999, False),
        ]:
            wav = uuid.uuid4()
            first = start + timedelta(seconds=offset)
            values = dict(
                id=wav,
                sensor=ids["sensor"],
                first=first,
                last=first + timedelta(seconds=duration),
                n=duration * 380,
                duration=duration,
                mean=mean,
                rms=math.sqrt(mean * mean + 4),
                hash="a" * 64,
                feature_hash=("a" if valid else "b") * 64,
            )
            db.execute(
                text("""INSERT INTO wav_file(id,sensor_id,started_at,ended_at,feature_status,timing_status,coverage_ratio,sample_rate,content_sha256,calibration_version)
              VALUES(:id,:sensor,:first,:last,'verified','complete',1,380,:hash,'nominal')"""),
                values,
            )
            db.execute(
                text("""INSERT INTO wav_feature(wav_file_id,source_sha256,sample_count,sample_rate,duration_seconds,mean,rms,minimum,maximum,value_unit,verified_at,calibration_version,coverage_ratio,timing_status)
              VALUES(:id,:feature_hash,:n,380,:duration,:mean,:rms,1,22,'mV',now(),'nominal',1,'complete')"""),
                values,
            )
        result = series(db, ids["sensor"], start, start + timedelta(hours=1), 3600, 1205)[0][
            "data"
        ][0]
        assert result["reading_count"] == 342000
        assert result["recording_count"] == 2
        assert result["value"] == round(40 / 3, 4)
        assert result["timing_overlap_seconds"] == 1
        assert result["coverage_ratio"] is None
        assert result["source_interval_end"] == (start + timedelta(seconds=899)).isoformat()
