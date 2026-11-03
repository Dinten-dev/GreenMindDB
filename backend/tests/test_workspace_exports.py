"""Main Gateway API honors the export dialog's explicit UTC bounds."""

import csv
import io
import zipfile
from datetime import UTC, datetime, timedelta

from app.models.timeseries import SensorReading


def test_main_chart_and_csv_share_half_open_bounds(client, db, setup_test_data, tenant_admin_token):
    sensor = setup_test_data["sensor"]
    start = datetime(2026, 10, 25, tzinfo=UTC)
    for offset in range(3):
        db.add(
            SensorReading(
                timestamp=start + timedelta(seconds=offset),
                sensor_id=sensor.id,
                kind="bio_signal",
                value=10 + offset,
                unit="mV",
            )
        )
    db.commit()
    params = {
        "from_dt": (start + timedelta(seconds=1)).isoformat(),
        "to_dt": (start + timedelta(seconds=2)).isoformat(),
        "resolution": "raw",
    }
    headers = {"Authorization": "Bearer " + tenant_admin_token}
    base = f"/api/v1/sensors/{sensor.id}"
    response = client.get(base + "/data", params=params, headers=headers)
    assert response.status_code == 200 and [p["value"] for p in response.json()[0]["data"]] == [11]
    response = client.get(base + "/export", params=params, headers=headers)
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        content = archive.read(archive.namelist()[0]).decode()
    rows = list(csv.DictReader(line for line in content.splitlines() if not line.startswith("#")))
    assert len(rows) == 1 and float(rows[0]["value"]) == 11
    assert (
        client.get(
            base + "/export", params={"from_dt": params["from_dt"]}, headers=headers
        ).status_code
        == 400
    )
    assert client.get(base + "/data", params=params).status_code == 401

    assert (
        client.get(
            base + "/data",
            params={
                "from_dt": "2020-01-01T00:00:00Z",
                "to_dt": "2026-01-01T00:00:00Z",
                "resolution": "1m",
            },
            headers=headers,
        ).status_code
        == 413
    )
