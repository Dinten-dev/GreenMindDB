"""Create a temporary, isolated staging fixture; never modify existing tenants."""

import hashlib
import io
import json
import os
import secrets
import uuid
import wave
from datetime import UTC, datetime, timedelta

import numpy as np
from app.auth import get_password_hash
from app.models.master import Gateway, Sensor, Zone
from app.models.timeseries import SensorReading
from app.models.user import Organization, Role, User
from app.models.wav_file import WavFile
from app.services.wav_feature_service import extract_and_verify_wav_features
from app.services.wav_service import _get_s3_client
from app.visualization.api import read_engine
from sqlalchemy.orm import Session

assert os.environ.get("VISUAL_TEST_ALLOWED") == "staging-20260915"
assert read_engine.url.username != "admin", "Production database identity is forbidden"
ids = {
    name: str(uuid.uuid4())
    for name in ("test", "org", "user", "zone", "gateway", "sensor")
}
ids["wavs"] = [str(uuid.uuid4()), str(uuid.uuid4())]
ids["keys"] = [f"visualization-qa/{ids['test']}/{i}.wav" for i in range(2)]
print(
    json.dumps(ids), flush=True
)  # Cleanup manifest is retained even if a later step fails.
mac = "02:" + ":".join(secrets.token_hex(1).upper() for _ in range(5))
with Session(read_engine) as db:
    db.add(
        Organization(
            id=uuid.UUID(ids["org"]), name="Temporary visualization QA " + ids["test"]
        )
    )
    db.flush()
    db.add(
        User(
            id=uuid.UUID(ids["user"]),
            organization_id=uuid.UUID(ids["org"]),
            email=f"{ids['test']}@visualization.invalid",
            name="Temporary visualization QA",
            password_hash=get_password_hash(secrets.token_urlsafe(48)),
            role=Role.OWNER,
            is_active=True,
            is_verified=True,
        )
    )
    db.add(
        Zone(
            id=uuid.UUID(ids["zone"]),
            organization_id=uuid.UUID(ids["org"]),
            name="Temporary visualization QA",
        )
    )
    db.flush()
    db.add(
        Gateway(
            id=uuid.UUID(ids["gateway"]),
            zone_id=uuid.UUID(ids["zone"]),
            hardware_id="visual-qa-" + ids["test"],
            name="Temporary visualization QA",
            status="offline",
            is_active=False,
        )
    )
    db.flush()
    db.add(
        Sensor(
            id=uuid.UUID(ids["sensor"]),
            gateway_id=uuid.UUID(ids["gateway"]),
            mac_address=mac,
            name="Temporary visualization QA",
            sensor_type="bioelectric",
            status="offline",
            sms_alerts_enabled=False,
        )
    )
    db.flush()
    # Two known signals cover recent minute windows and historical ten-minute windows.
    samples = np.round(
        1000 + 40 * np.sin(2 * np.pi * 3 * np.arange(380 * 60) / 380)
    ).astype("<i2")
    samples[5700] = 1200
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(380)
        wav.writeframes(samples.tobytes())
    payload = output.getvalue()
    for i, days in enumerate((2, 14)):
        start = (datetime.now(UTC) - timedelta(days=days)).replace(
            hour=12, minute=0, second=20, microsecond=0
        )
        _get_s3_client().put_object(
            Bucket="greenmind-raw",
            Key=ids["keys"][i],
            Body=payload,
            ContentType="audio/wav",
        )
        row = WavFile(
            id=uuid.UUID(ids["wavs"][i]),
            sensor_id=uuid.UUID(ids["sensor"]),
            gateway_id=uuid.UUID(ids["gateway"]),
            sensor_mac=mac,
            s3_key=ids["keys"][i],
            content_sha256=hashlib.sha256(payload).hexdigest(),
            sample_rate=380,
            duration_seconds=60,
            coverage_ratio=1,
            timing_status="complete",
            file_size_bytes=len(payload),
            started_at=start,
            ended_at=start + timedelta(seconds=60),
            feature_status="processing",
            feature_started_at=datetime.now(UTC),
            feature_attempts=1,
            pcm_scale_mv=1,
            pcm_offset_mv=0,
            calibration_version="visualization-test-only",
        )
        db.add(row)
        for offset, value in enumerate(
            samples.astype(float).reshape(60, 380).mean(axis=1)
        ):
            db.add(
                SensorReading(
                    sensor_id=uuid.UUID(ids["sensor"]),
                    timestamp=start + timedelta(seconds=offset),
                    kind="bio_signal",
                    value=float(value),
                    unit="mV",
                )
            )
        db.commit()
        extract_and_verify_wav_features(db, row)
