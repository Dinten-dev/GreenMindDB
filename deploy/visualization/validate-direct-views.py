"""Authenticated real-device checks; run inside the visualization API, no secrets printed."""

import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from datetime import timedelta

from app.auth import create_access_token
from app.visualization.api import read_engine
from app.visualization.direct import resources
from sqlalchemy import text

base = os.environ.get("VISUAL_VALIDATE_BASE_URL", "https://test.green-mind.ch")
assert base in ("https://test.green-mind.ch", "http://127.0.0.1:8000")


def request(path, token=None, expected=200):
    headers = {"Authorization": "Bearer " + token} if token else {}
    try:
        with urllib.request.urlopen(
            urllib.request.Request(base + path, headers=headers), timeout=30
        ) as r:
            code, payload = r.status, r.read(8_000_001)
    except urllib.error.HTTPError as err:
        code, payload = err.code, err.read(512)
    assert code == expected, f"{path.split('?')[0]} expected {expected}, got {code}"
    assert len(payload) <= 8_000_000
    return payload


engine, _ = resources()
with engine.connect() as db:
    device = (
        db.execute(
            text(
                "SELECT d.id,d.organization_id FROM direct_device d JOIN direct_enrollment e ON e.device_id=d.id WHERE lower(e.hardware_id)='14:c1:9f:d9:42:9c'"
            )
        )
        .mappings()
        .one()
    )
    worker = (
        db.execute(
            text("SELECT status,updated_at FROM direct_visual_worker WHERE id=1")
        )
        .mappings()
        .one()
    )
with read_engine.connect() as db:
    user = db.execute(
        text(
            "SELECT id FROM users WHERE organization_id=:org AND is_active=true AND is_verified=true ORDER BY created_at LIMIT 1"
        ),
        {"org": device["organization_id"]},
    ).scalar_one()
token = create_access_token({"sub": str(user)}, expires_delta=timedelta(minutes=3))
route = "/api/v1/visualization/direct/" + device["id"]
request(route + "/data", expected=401)
request(
    "/api/v1/visualization/direct/" + str(uuid.uuid4()) + "/data", token, expected=404
)
expired = create_access_token({"sub": str(user)}, expires_delta=timedelta(seconds=-1))
request(route + "/data", expired, expected=401)
series = json.loads(request(route + "/data?range=1h", token))
assert series and series[0]["data"] and series[0]["source"] == "direct"
assert all(
    p["minimum"] <= p["value"] + 0.001 <= p["maximum"] + 0.002 and p["rms"] is not None
    for p in series[0]["data"]
)
waveform = json.loads(
    request(
        route
        + "/waveform?"
        + urllib.parse.urlencode(
            {"at": series[0]["data"][-10]["timestamp"], "seconds": 2}
        ),
        token,
    )
)
assert (
    waveform["sample_rate"] == 380
    and len(waveform["data"]) > 0
    and waveform["unit"] == "mV"
)
content = request(route + "/export?range=1h", token).decode()
assert (
    "resolution_seconds" in content
    and "coverage_ratio" in content
    and len(content.splitlines()) > 2
)
files = json.loads(request(route + "/recordings?range=7d", token))
assert files
file = files[0]
payload = request(
    route + f"/wav/{file['segment_id']}/{file['revision']}/{file['run']}", token
)
with wave.open(io.BytesIO(payload), "rb") as wav:
    assert (
        wav.getframerate() == 380
        and wav.getnchannels() == 1
        and wav.getsampwidth() == 2
    )
    frames = wav.getnframes()
with engine.connect() as db:
    projections = (
        db.execute(
            text(
                "SELECT count(*) AS segments,sum(p.n) AS frames FROM direct_visual_point p WHERE device_id=:id"
            ),
            {"id": device["id"]},
        )
        .mappings()
        .one()
    )
    latest = db.execute(
        text("SELECT max(received_at) FROM direct_chunk WHERE device_id=:id"),
        {"id": device["id"]},
    ).scalar_one()
print(
    json.dumps(
        {
            "passed": True,
            "checked_at": time.time(),
            "device_id": device["id"],
            "series_points": len(series[0]["data"]),
            "original_waveform_samples": len(waveform["data"]),
            "wav_files_available": len(files),
            "wav_download_frames": frames,
            "worker": dict(worker),
            "projected_samples": int(projections["frames"]),
            "latest_receive_age_seconds": time.time() - latest,
            "checks": [
                "authentication",
                "expired_token",
                "missing_sensor",
                "real_direct_series",
                "original_waveform",
                "csv",
                "wav_download",
            ],
        },
        default=str,
    )
)
