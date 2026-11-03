"""Bounded historical previews/exports preserve exact UTC windows and authorization."""

import io
import zipfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.direct.assembler import assemble_one
from app.visualization.api import app

pytest_plugins = ("tests.visualization.test_direct", "tests.visualization.test_postgres")
pytestmark = pytest.mark.integration


def test_direct_recording_picker_uses_explicit_history_bounds(live):
    x = live
    assert assemble_one(x.engine, x.settings, x.store)
    base = f"/api/v1/visualization/direct/{x.device}/recordings"
    start = datetime.fromtimestamp(x.start, UTC)
    bounds = {"from_dt": start.isoformat(), "to_dt": (start + timedelta(minutes=10)).isoformat()}
    response = x.client.get(base, params=bounds)
    assert response.status_code == 200 and len(response.json()) == 1
    later = {
        "from_dt": (start + timedelta(days=1)).isoformat(),
        "to_dt": (start + timedelta(days=2)).isoformat(),
    }
    assert x.client.get(base, params=later).json() == []
    assert x.client.get(base, params={"from_dt": bounds["from_dt"]}).status_code == 400
    assert x.client.get(base, params={**bounds, "to_dt": bounds["from_dt"]}).status_code == 400


def test_gateway_preview_and_csv_use_the_chosen_half_open_window(fixture):
    engine, ids, start, add = fixture
    for offset in range(3):
        add(start + timedelta(seconds=offset), 10 + offset)

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        organization_id=ids["org"],
        role="admin",
        is_active=True,
        is_verified=True,
        email="reader@example.test",
    )
    bounds = {
        "range": "1h",
        "from_dt": (start + timedelta(seconds=1)).isoformat(),
        "to_dt": (start + timedelta(seconds=2)).isoformat(),
    }
    with TestClient(app) as client:
        base = f"/api/v1/sensors/{ids['sensor']}"
        data = client.get(base + "/data", params=bounds)
        assert data.status_code == 200 and [p["value"] for p in data.json()[0]["data"]] == [11]
        response = client.get(base + "/export", params=bounds)
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            csv = archive.read(archive.namelist()[0]).decode()
        assert (start + timedelta(seconds=1)).isoformat() in csv
        assert (start + timedelta(seconds=2)).isoformat() not in csv
        assert (
            client.get(base + "/export", params={"from_dt": bounds["from_dt"]}).status_code == 400
        )
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            organization_id=None, role="member", email="reader@example.test"
        )
        assert client.get(base + "/data", params=bounds).status_code == 403
        assert client.get(base + "/export", params=bounds).status_code == 403
