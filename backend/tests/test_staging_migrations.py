"""Opt-in upgrade test against an empty, disposable local Staging-version DB."""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.auth import get_password_hash
from app.database import get_db
from app.main import app

pytestmark = pytest.mark.integration


def test_upgrade_preserves_old_data_and_accepts_compressed_history(client, monkeypatch):
    url = os.getenv("STAGING_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires an empty disposable local TimescaleDB")
    parsed = make_url(url)
    assert parsed.host in {"127.0.0.1", "localhost"}
    assert parsed.database == "migration_test"
    engine = create_engine(url)
    assert inspect(engine).get_table_names() == [], "Refusing a nonempty database"
    root = Path(__file__).resolve().parents[1]

    def upgrade(revision):
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=root,
            env=os.environ | {"DATABASE_URL": url},
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )

    with engine.begin() as connection:
        connection.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
    upgrade("0019")
    ids = {key: uuid.uuid4() for key in ("org", "zone", "gateway", "sensor", "wav")}
    start = datetime.now(UTC) - timedelta(days=91)
    params = ids | {
        "start": start,
        "end": start + timedelta(seconds=1),
        "key": get_password_hash("legacy-migration-test-key"),
    }
    with engine.begin() as connection:
        statements = [
            "INSERT INTO organization(id,name) VALUES (:org,'Migration test')",
            "INSERT INTO zone(id,organization_id,name,location,zone_type) "
            "VALUES (:zone,:org,'Test zone','Local test','GREENHOUSE')",
            "INSERT INTO gateway(id,zone_id,hardware_id,api_key_hash) "
            "VALUES (:gateway,:zone,'migration-test-gateway',:key)",
            "INSERT INTO sensor(id,gateway_id,mac_address,sensor_type) "
            "VALUES (:sensor,:gateway,'AA:BB:CC:DD:EE:90','leaf_voltage')",
            "INSERT INTO sensor_reading(timestamp,sensor_id,kind,value,unit) "
            "VALUES (:start,:sensor,'leaf_voltage',321.125,'mV')",
            "INSERT INTO wav_file(id,sensor_id,gateway_id,sensor_mac,s3_key,"
            "duration_seconds,file_size_bytes,started_at,ended_at) "
            "VALUES (:wav,:sensor,:gateway,'AA:BB:CC:DD:EE:90',"
            "'wav/legacy-preserved.wav',1,804,:start,:end)",
        ]
        for statement in statements:
            connection.execute(text(statement), params)
    upgrade("head")
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0023"
        assert connection.scalar(text("SELECT value FROM sensor_reading")) == 321.125
        wav = connection.execute(
            text("SELECT s3_key,raw_deleted_at,pcm_encoding_version FROM wav_file")
        ).one()
        assert tuple(wav) == ("wav/legacy-preserved.wav", None, "unsigned-mv-linear-int16-v1")
        connection.execute(
            text("SELECT compress_chunk(chunk, true) FROM show_chunks('sensor_reading') AS chunk")
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM timescaledb_information.chunks WHERE is_compressed")
            )
            >= 1
        )

    sessions = sessionmaker(bind=engine)

    def migrated_database():
        with sessions() as database:
            yield database

    monkeypatch.setitem(app.dependency_overrides, get_db, migrated_database)
    body = {
        "measurement_id": str(uuid.uuid4()),
        "gateway_serial": "migration-test-gateway",
        "readings": [
            {
                "sensor_mac": "AA:BB:CC:DD:EE:90",
                "sensor_kind": "leaf_voltage",
                "value": 456.75,
                "unit": "mV",
                "timestamp": (start + timedelta(seconds=1)).isoformat(),
            }
        ],
    }
    headers = {"X-Api-Key": "legacy-migration-test-key"}
    try:
        response = client.post("/api/v1/ingest", json=body, headers=headers)
        assert response.status_code == 201, response.text
        assert response.json()["ingested"] == 1
        assert (
            client.post("/api/v1/ingest", json=body, headers=headers).json()["status"]
            == "duplicate"
        )
        with engine.connect() as connection:
            assert list(
                connection.scalars(text("SELECT value FROM sensor_reading ORDER BY timestamp"))
            ) == [321.125, 456.75]
    finally:
        engine.dispose()
