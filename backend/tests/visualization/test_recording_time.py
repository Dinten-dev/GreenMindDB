"""Real read-side projection, integrity, raw independence and matched UTC exports."""

import io
import wave
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.direct.assembler import assemble_one
from app.direct.models import Revision
from app.visualization import direct

pytest_plugins = ("tests.visualization.test_direct",)
pytestmark = pytest.mark.integration


def test_capture_summary_recording_hash_and_export_exact_bounds(live):
    x = live
    assert assemble_one(x.engine, x.settings, x.store)
    assert direct.refresh_segment(x.engine, x.store, x.segment, x.start + 1200)
    start = datetime.fromtimestamp(x.start, UTC)
    end = start + timedelta(seconds=600)
    params = {"range": "1h", "from_dt": start.isoformat(), "to_dt": end.isoformat()}
    writes = []

    def observe(_, __, statement, *args):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE", "ALTER"}:
            writes.append(statement)

    event.listen(x.engine, "before_cursor_execute", observe)
    try:
        base = f"/api/v1/visualization/direct/{x.device}"
        response = x.client.get(base + "/data", params=params)
        assert response.status_code == 200, response.text
        time = response.json()[0]["time_info"]
        assert time["anchor_status"] == "available"
        assert time["physical_accuracy_verified"] is False
        assert time["coverage_status"] == "incomplete" and time["coverage_ratio"] < 1
        assert time["requested_from"] == start.isoformat()
        csv = x.client.get(base + "/export", params=params)
        assert csv.status_code == 200
        assert start.strftime("%Y%m%dT%H%M%S") in csv.headers["Content-Disposition"]
        assert end.strftime("%Y%m%dT%H%M%S") in csv.headers["Content-Disposition"]
        assert "time_basis,anchor_status,physical_time_accuracy_verified" in csv.text
        files = x.client.get(base + "/recordings?range=1h").json()
        assert len(files) == 1 and len(files[0]["time_info"]["sha256"]) == 64
        file = files[0]
        assert file["time_info"]["session_id"] == x.session
        wav = x.client.get(f"{base}/wav/{file['segment_id']}/{file['revision']}/{file['run']}")
        assert wav.status_code == 200 and "Z_" in wav.headers["Content-Disposition"]
        with wave.open(io.BytesIO(wav.content)) as recording:
            assert recording.readframes(380) == x.raw.tobytes()
        assert not writes
    finally:
        event.remove(x.engine, "before_cursor_execute", observe)


def test_changed_manifest_is_rejected_instead_of_silently_retimed(live):
    x = live
    assert assemble_one(x.engine, x.settings, x.store)
    with Session(x.engine) as db, db.begin():
        row = db.query(Revision).one()
        row.manifest = {**row.manifest, "session_id": "not-the-original-session"}
    response = x.client.get(f"/api/v1/visualization/direct/{x.device}/recordings?range=1h")
    assert response.status_code == 503
