"""Real PostgreSQL typed restore contract, exclusively in empty local fixtures."""

import os
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from app.raw_archive import recovery
from app.raw_archive.policy import checksum

pytestmark = pytest.mark.integration


def test_actual_empty_postgres_restore_preserves_uuid_json_decimal_timestamp_bytea(
    tmp_path, monkeypatch
):
    if os.environ.get("SKIP_DOCKER_TESTS") == "1":
        pytest.skip("Local Docker disabled")
    docker = shutil.which("docker")
    assert docker
    name, password = "gm-recovery-contract-" + uuid.uuid4().hex, uuid.uuid4().hex
    subprocess.run(
        [
            docker,
            "run",
            "-d",
            "--name",
            name,
            "--label",
            "greenmind.recovery-test=true",
            "--memory",
            "256m",
            "--cpus",
            "0.5",
            "-p",
            "127.0.0.1::5432",
            "-e",
            "POSTGRES_PASSWORD=" + password,
            "-e",
            "POSTGRES_DB=greenmind_restore_contract",
            "postgres:16",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    engine = readonly = None
    try:
        address = subprocess.check_output(
            [docker, "port", name, "5432"], text=True, timeout=5
        ).strip()
        url = (
            "postgresql+psycopg2://postgres:"
            + password
            + "@"
            + address
            + "/greenmind_restore_contract"
        )
        engine = create_engine(url, connect_args={"connect_timeout": 2})
        for attempt in range(30):
            try:
                with engine.connect() as db:
                    db.execute(text("SELECT 1"))
                break
            except Exception:
                if attempt == 29:
                    raise
                time.sleep(0.2)
        with engine.begin() as db:
            db.execute(
                text(
                    "CREATE TABLE wav_file(id uuid PRIMARY KEY, metadata jsonb, amount numeric(20,8), "
                    "at timestamptz, payload bytea)"
                )
            )
            db.execute(
                text("INSERT INTO wav_file VALUES (:id,CAST(:data AS jsonb),:amount,:at,:payload)"),
                {
                    "id": str(uuid.uuid4()),
                    "data": '{"unit":"mV","channels":[1,2]}',
                    "amount": Decimal("123456789.12345678"),
                    "at": datetime(2026, 10, 3, tzinfo=UTC),
                    "payload": b"\x00\xffexact",
                },
            )
        readonly = create_engine(
            url, connect_args={"options": "-c default_transaction_read_only=on"}
        )
        monkeypatch.setattr(recovery, "TABLES", {"gateway": ("wav_file",), "direct": ()})
        path = tmp_path / "fixture-gateway.jsonl.gz"
        entry = recovery.backup_metadata(readonly, "gateway", path)
        assert entry["rows"] == 1
        # Empty only this named local fixture, never a production database.
        with engine.begin() as db:
            db.execute(text("TRUNCATE wav_file"))
        assert recovery.restore_metadata(path, checksum(path), engine) == 1
        with engine.connect() as db:
            row = db.execute(text("SELECT * FROM wav_file")).mappings().one()
            assert row["metadata"] == {"unit": "mV", "channels": [1, 2]}
            assert row["amount"] == Decimal("123456789.12345678")
            assert bytes(row["payload"]) == b"\x00\xffexact"
            assert row["at"].tzinfo is not None and isinstance(row["id"], uuid.UUID)
    finally:
        if readonly:
            readonly.dispose()
        if engine:
            engine.dispose()
        label = subprocess.check_output(
            [
                docker,
                "inspect",
                "--format",
                '{{index .Config.Labels "greenmind.recovery-test"}}',
                name,
            ],
            text=True,
            timeout=5,
        ).strip()
        assert label == "true"
        subprocess.run([docker, "rm", "-f", name], check=True, capture_output=True, timeout=10)
