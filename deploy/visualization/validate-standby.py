"""Read-only end-to-end checks inside the read API container. Never print credentials."""

import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import timedelta

from app.auth import create_access_token
from app.visualization.api import read_engine
from sqlalchemy import text

base = "http://127.0.0.1:8000"
checks = []


def request(path, token=None, expected=200):
    headers = {"Authorization": "Bearer " + token} if token else {}
    try:
        with urllib.request.urlopen(
            urllib.request.Request(base + path, headers=headers), timeout=20
        ) as response:
            code, payload = response.status, response.read(8 * 1024**2)
    except urllib.error.HTTPError as error:
        code, payload = error.code, error.read(1024)
    assert code == expected, f"{path.split('?')[0]}: expected {expected}, got {code}"
    return payload


health = json.loads(request("/health"))
checks.append("read_api_health")
with read_engine.connect() as db:
    item = (
        db.execute(
            text("""SELECT w.sensor_id,w.started_at,w.sample_rate,u.id AS user_id
      FROM wav_file w JOIN sensor s ON s.id=w.sensor_id JOIN gateway g ON g.id=s.gateway_id
      JOIN zone z ON z.id=g.zone_id JOIN users u ON u.organization_id=z.organization_id
      WHERE w.feature_status='verified' AND w.raw_deleted_at IS NULL AND w.timing_status IN ('complete','inferred')
      AND w.coverage_ratio>=0.999 AND u.is_active=true AND u.is_verified=true
      ORDER BY w.started_at DESC LIMIT 1""")
        )
        .mappings()
        .first()
    )
    assert item, (
        "No verified sensor/WAV/account combination available for a real-data check"
    )
    item = dict(item)
token = create_access_token(
    {"sub": str(item["user_id"])}, expires_delta=timedelta(minutes=3)
)
route = f"/api/v1/sensors/{item['sensor_id']}/data?date={item['started_at'].date()}"
request(route, expected=401)
request("/api/v1/sensors/" + str(uuid.uuid4()) + "/data", token, expected=404)
expired = create_access_token(
    {"sub": str(item["user_id"])}, expires_delta=timedelta(seconds=-1)
)
request(route, expired, expected=401)
checks.append("authentication_and_missing_sensor")
data = json.loads(request(route, token))
assert data and all(row["sensor_id"] == str(item["sensor_id"]) for row in data)
assert any(row["data"] for row in data)
checks.append("authenticated_real_sensor_data")
at = item["started_at"] + timedelta(seconds=1)
waveform = json.loads(
    request(
        f"/api/v1/visualization/sensors/{item['sensor_id']}/waveform?"
        + urllib.parse.urlencode({"at": at.isoformat(), "seconds": 2}),
        token,
    )
)
assert (
    waveform["source"] == "verified_wav"
    and waveform["sample_rate"] == item["sample_rate"]
    and waveform["data"]
)
checks.append("verified_wav_original_samples")
export = request(f"/api/v1/sensors/{item['sensor_id']}/export?range=30d", token)
with zipfile.ZipFile(io.BytesIO(export)) as package:
    assert package.namelist()
    content = package.read(package.namelist()[0]).decode()
    assert (
        "# Zone:" in content
        and "resolution_seconds" in content
        and "coverage_ratio" in content
    )
checks.append("bounded_authenticated_csv_with_resolution")
print(
    json.dumps(
        {
            "passed": True,
            "checked_at": time.time(),
            "checks": checks,
            "worker": health["worker"],
            "waveform_samples": len(waveform["data"]),
            "data_points": sum(len(row["data"]) for row in data),
        },
        default=str,
    )
)
