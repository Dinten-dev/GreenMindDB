import io
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from app.direct.config import DirectSettings
from app.models.timeseries import SensorReading
from tests.direct.conftest import build_pipeline


@pytest.mark.parametrize("legacy_format", ["wav", "readings"])
def test_delayed_legacy_upload_survives_direct_backpressure(
    client, db, setup_test_data, mocker, tmp_path, legacy_format
):
    cfg = DirectSettings(
        database_url=f"sqlite:///{tmp_path}/direct.db",
        ingest_enabled=True,
        require_tls=False,
        max_spool_bytes=1024,
        artifact_root=tmp_path / "artifacts",
    )
    p = build_pipeline(cfg)
    mocker.patch("app.routers.wav.wav_service.upload_wav")
    raw = io.BytesIO()
    with wave.open(raw, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(380)
        wav.writeframes(b"\x01\x00" * 380)
    start = datetime.now(UTC) - timedelta(days=91)
    measurement_id = str(uuid.uuid4())

    def old_upload():
        if legacy_format == "readings":
            return client.post(
                "/api/v1/ingest",
                headers={"X-Api-Key": "ci-api-key"},
                json={
                    "measurement_id": measurement_id,
                    "gateway_serial": setup_test_data["gateway"].hardware_id,
                    "readings": [
                        {
                            "sensor_mac": setup_test_data["sensor"].mac_address,
                            "sensor_kind": "leaf_voltage",
                            "value": 123.0,
                            "unit": "mV",
                            "timestamp": start.isoformat(),
                        }
                    ],
                },
            )
        return client.post(
            "/api/v1/wav/upload",
            headers={"X-Api-Key": "ci-api-key"},
            files={"file": ("old-gateway.wav", raw.getvalue(), "audio/wav")},
            data={
                "sensor_mac": setup_test_data["sensor"].mac_address,
                "gateway_serial": setup_test_data["gateway"].hardware_id,
                "started_at": start.isoformat(),
                "ended_at": (start + timedelta(seconds=1)).isoformat(),
                # All new optional fields omitted, including sample_rate.
            },
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            legacy = pool.submit(old_upload)
            direct = pool.submit(p.upload, bytes(1200))
            assert legacy.result().status_code == 201
            assert direct.result().status_code == 503
        assert old_upload().json()["status"] == "duplicate"
        p.cfg.ingest_enabled = False
        assert old_upload().status_code == 201
        if legacy_format == "readings":
            row = db.query(SensorReading).one()
            assert row.timestamp.replace(tzinfo=UTC) == start
            assert row.value == 123.0
    finally:
        p.client.close()
        p.engine.dispose()
