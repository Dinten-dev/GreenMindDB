"""Check the known staging fixture through the actual read API and background output."""

import json
import urllib.request
from datetime import timedelta

from app.auth import create_access_token
from app.visualization.api import read_engine
from sqlalchemy import text

with read_engine.connect() as db:
    item = (
        db.execute(
            text("""SELECT s.id AS sensor_id,u.id AS user_id,min(w.started_at) AS oldest
    FROM sensor s JOIN gateway g ON g.id=s.gateway_id JOIN zone z ON z.id=g.zone_id
    JOIN organization o ON o.id=z.organization_id JOIN users u ON u.organization_id=o.id
    JOIN wav_file w ON w.sensor_id=s.id WHERE o.name LIKE 'Temporary visualization QA %'
    GROUP BY s.id,u.id""")
        )
        .mappings()
        .one()
    )
    assert (
        db.execute(
            text("SELECT sum(n) FROM visual_reading WHERE sensor_id=:sid"),
            {"sid": item["sensor_id"]},
        ).scalar_one()
        == 60
    )
    assert (
        db.execute(
            text(
                "SELECT count(*) FROM visual_wav v JOIN wav_file w ON w.id=v.wav_id WHERE w.sensor_id=:sid AND v.status='verified'"
            ),
            {"sid": item["sensor_id"]},
        ).scalar_one()
        == 2
    )
token = create_access_token(
    {"sub": str(item["user_id"])}, expires_delta=timedelta(minutes=2)
)
url = f"http://127.0.0.1:8000/api/v1/sensors/{item['sensor_id']}/data?date={item['oldest'].date()}"
with urllib.request.urlopen(
    urllib.request.Request(url, headers={"Authorization": "Bearer " + token}),
    timeout=15,
) as response:
    data = json.load(response)
point = data[0]["data"][0]
assert point["resolution_seconds"] == 600 and point["reading_count"] == 60
assert (
    point["minimum"] == 960
    and point["maximum"] == 1200
    and point["signal_source"] == "wav"
)
assert abs(point["coverage_ratio"] - 0.1) < 1e-6
print(
    json.dumps(
        {
            "passed": True,
            "resolution_seconds": point["resolution_seconds"],
            "source_rows": point["reading_count"],
            "preserved_peak_mv": point["maximum"],
            "coverage_ratio": point["coverage_ratio"],
        }
    )
)
