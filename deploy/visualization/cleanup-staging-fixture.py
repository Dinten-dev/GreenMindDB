"""Delete only the exact temporary records and objects from the fixture manifest."""

import json
import os
import sys
import uuid

from app.models.master import Gateway, Sensor, Zone
from app.models.user import Organization, User
from app.models.wav_file import WavFile
from app.services.wav_service import _get_s3_client
from app.visualization.api import read_engine
from sqlalchemy import text
from sqlalchemy.orm import Session

assert os.environ.get("VISUAL_TEST_ALLOWED") == "staging-20260915"
assert read_engine.url.username != "admin", "Production database identity is forbidden"
ids = json.load(sys.stdin)
assert all(key.startswith(f"visualization-qa/{ids['test']}/") for key in ids["keys"])
with Session(read_engine) as db:
    org = db.get(Organization, uuid.UUID(ids["org"]))
    if org:
        assert org.name == "Temporary visualization QA " + ids["test"]
        sensor = db.get(Sensor, uuid.UUID(ids["sensor"]))
        if sensor:
            assert (
                str(sensor.gateway_id) == ids["gateway"]
                and sensor.name == "Temporary visualization QA"
            )
        wav_ids = [uuid.UUID(value) for value in ids["wavs"]]
        for wav in db.query(WavFile).filter(WavFile.id.in_(wav_ids)):
            assert str(wav.sensor_id) == ids["sensor"] and wav.s3_key in ids["keys"]
        db.execute(
            text("DELETE FROM sensor_reading WHERE sensor_id=:id"),
            {"id": ids["sensor"]},
        )
        db.query(WavFile).filter(WavFile.id.in_(wav_ids)).delete(
            synchronize_session=False
        )
        for model, key in (
            (Sensor, "sensor"),
            (Gateway, "gateway"),
            (Zone, "zone"),
            (User, "user"),
            (Organization, "org"),
        ):
            db.query(model).filter(model.id == uuid.UUID(ids[key])).delete(
                synchronize_session=False
            )
        db.commit()
response = _get_s3_client().delete_objects(
    Bucket="greenmind-raw", Delete={"Objects": [{"Key": key} for key in ids["keys"]]}
)
assert not response.get("Errors"), "Temporary object cleanup incomplete"
print("Only the isolated staging fixture and its two WAV objects were removed.")
