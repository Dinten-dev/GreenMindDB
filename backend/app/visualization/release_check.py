"""Authenticated readiness checks inside the read container; never prints credentials."""

import argparse
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from datetime import datetime, timedelta

from sqlalchemy import text

from app.auth import COOKIE_NAME, create_access_token
from app.visualization.api import read_engine
from app.visualization.direct import resources


def state():
    with read_engine.connect() as db:
        return {
            "compacted_chunks": db.execute(
                text("SELECT count(*) FROM visual_chunk WHERE status='compacted'")
            ).scalar_one()
        }


def check():
    base = os.environ.get("RELEASE_CHECK_BASE", "http://127.0.0.1:8000")
    domain = os.environ["FRONTEND_URL"].rstrip("/")
    if domain not in ("https://green-mind.ch", "https://test.green-mind.ch"):
        raise RuntimeError("Unsupported dashboard origin")
    if base not in ("http://127.0.0.1:8000", domain):
        raise RuntimeError("Unsupported validation origin")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise RuntimeError("Readiness requests must not redirect credentials")

    opener = urllib.request.build_opener(NoRedirect)

    def request(path, token=None, expected=200, cookie=False):
        headers = (
            (
                {"Cookie": COOKIE_NAME + "=" + token}
                if cookie
                else {"Authorization": "Bearer " + token}
            )
            if token
            else {}
        )
        try:
            with opener.open(
                urllib.request.Request(base + path, headers=headers),  # noqa: S310 - origins allowlisted above
                timeout=20,
            ) as response:
                code, payload = response.status, response.read(8_000_001)
        except urllib.error.HTTPError as error:
            code, payload = error.code, error.read(1024)
        if code != expected or len(payload) > 8_000_000:
            raise RuntimeError(
                "Readiness request failed: " + path.split("?")[0] + " status=" + str(code)
            )
        return payload

    # A graceful nginx reload may briefly serve old workers. Never accept their
    # successful health response as proof of the new candidate's public route.
    expected_revision = os.environ.get("RELEASE_REVISION")
    expected_id = os.environ.get("RELEASE_ID")
    if not expected_revision or not expected_id:
        raise RuntimeError("Release revision missing")
    for attempt in range(20):
        try:
            health = json.loads(
                request("/visualization-health" if base.startswith("https:") else "/health")
            )
        except (RuntimeError, urllib.error.URLError, json.JSONDecodeError):
            if not base.startswith("https:") or attempt == 19:
                raise
            health = {}
        if (
            health.get("release_revision") == expected_revision
            and health.get("release_id") == expected_id
        ):
            break
        if attempt == 19:
            raise RuntimeError("Public route is not the candidate release")
        time.sleep(0.2)
    worker = health.get("worker") or {}
    age = (
        time.time() - datetime.fromisoformat(worker["updated_at"]).timestamp()
        if worker.get("updated_at")
        else float("inf")
    )
    if (
        age < -5
        or age > 180
        or worker.get("status") not in ("healthy", "working", "paused_for_host_load")
    ):
        raise RuntimeError("Legacy projection worker unavailable")
    started_after = float(os.environ.get("RELEASE_WORKERS_STARTED_AFTER", "0"))
    if time.time() - age < started_after:
        raise RuntimeError("Waiting for candidate worker heartbeat")
    with read_engine.connect() as db:
        item = (
            db.execute(
                text("""SELECT s.id AS sensor_id,u.id AS user_id,w.started_at,w.sample_rate
            FROM wav_file w JOIN sensor s ON s.id=w.sensor_id JOIN gateway g ON g.id=s.gateway_id
            JOIN zone z ON z.id=g.zone_id JOIN users u ON u.organization_id=z.organization_id
            WHERE w.feature_status='verified' AND w.raw_deleted_at IS NULL
              AND w.timing_status IN ('complete','inferred') AND w.coverage_ratio>=0.999
              AND u.is_active AND u.is_verified ORDER BY w.started_at DESC LIMIT 1""")
            )
            .mappings()
            .first()
        )
        if not item:
            raise RuntimeError("No verified Gateway WAV/account available for acceptance")
    token = create_access_token({"sub": str(item["user_id"])}, expires_delta=timedelta(minutes=3))
    route = "/api/v1/sensors/" + str(item["sensor_id"])
    request(route + "/data", expected=401)
    request("/api/v1/sensors/" + str(uuid.uuid4()) + "/data", token, expected=404)
    expired = create_access_token(
        {"sub": str(item["user_id"])}, expires_delta=timedelta(seconds=-1)
    )
    request(route + "/data", expired, expected=401)
    data = json.loads(
        request(route + "/data?date=" + str(item["started_at"].date()), token, cookie=True)
    )
    if not any(series["data"] for series in data):
        raise RuntimeError("Gateway series is empty")
    at = item["started_at"] + timedelta(seconds=1)
    waveform = json.loads(
        request(
            "/api/v1/visualization/sensors/"
            + str(item["sensor_id"])
            + "/waveform?"
            + urllib.parse.urlencode({"at": at.isoformat(), "seconds": 2}),
            token,
        )
    )
    if not waveform["data"] or waveform["sample_rate"] != item["sample_rate"]:
        raise RuntimeError("Gateway waveform mismatch")
    # Direct storage must be reachable with the restricted identity before activation.
    engine, store = resources()
    store.client.list_objects_v2(Bucket=store.settings.s3_bucket, Prefix="direct/", MaxKeys=1)
    with engine.connect() as db:
        heartbeat = (
            db.execute(
                text(
                    "SELECT status,extract(epoch from updated_at) AS updated FROM direct_visual_worker WHERE id=1"
                )
            )
            .mappings()
            .one()
        )
        if (
            heartbeat["status"] not in ("healthy", "paused_for_host_load")
            or not -5 <= time.time() - float(heartbeat["updated"]) <= 120
            or float(heartbeat["updated"]) < started_after
        ):
            raise RuntimeError("Direct projection worker unavailable")
        devices = (
            db.execute(
                text("SELECT id,organization_id FROM direct_device WHERE active=true LIMIT 10")
            )
            .mappings()
            .all()
        )
    direct_checked = False
    for device in devices:
        with read_engine.connect() as db:
            user = db.execute(
                text(
                    "SELECT id FROM users WHERE organization_id=:org AND is_active AND is_verified LIMIT 1"
                ),
                {"org": device["organization_id"]},
            ).scalar_one_or_none()
        if user is None:
            continue
        direct_token = create_access_token({"sub": str(user)}, expires_delta=timedelta(minutes=3))
        direct_route = "/api/v1/visualization/direct/" + device["id"]
        request(direct_route + "/data", expected=401)
        series = json.loads(request(direct_route + "/data?range=7d", direct_token, cookie=True))
        files = json.loads(request(direct_route + "/recordings?range=7d", direct_token))
        if not files or not any(s["data"] for s in series):
            continue
        file = files[0]
        payload = request(
            direct_route + f"/wav/{file['segment_id']}/{file['revision']}/{file['run']}",
            direct_token,
        )
        with wave.open(io.BytesIO(payload), "rb") as wav:
            if wav.getnframes() <= 0 or wav.getframerate() != file["sample_rate"]:
                raise RuntimeError("Direct WAV mismatch")
        direct_checked = True
        break
    if devices and not direct_checked:
        raise RuntimeError("Existing Direct devices have no verified visible data/WAV")
    return {
        "passed": True,
        "gateway_waveform_samples": len(waveform["data"]),
        "direct_data_verified": direct_checked,
        "direct_bootstrap_without_devices": not devices,
        "cookie_authentication": True,
        "compacted_chunks": state()["compacted_chunks"],
    }


def workers_ready():
    after = float(os.environ.get("RELEASE_WORKERS_STARTED_AFTER", "0"))
    with read_engine.connect() as db:
        legacy = (
            db.execute(
                text(
                    "SELECT status,extract(epoch from updated_at) AS updated FROM visual_worker WHERE name='compactor'"
                )
            )
            .mappings()
            .one()
        )
    engine, _ = resources()
    with engine.connect() as db:
        direct = (
            db.execute(
                text(
                    "SELECT status,extract(epoch from updated_at) AS updated FROM direct_visual_worker WHERE id=1"
                )
            )
            .mappings()
            .one()
        )
    for item in (legacy, direct):
        if (
            item["status"] not in ("healthy", "working", "paused_for_host_load")
            or float(item["updated"]) < after
            or time.time() - float(item["updated"]) > 180
        ):
            raise RuntimeError("Waiting for fresh healthy candidate worker heartbeats")
    return {"passed": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", action="store_true")
    parser.add_argument("--workers-ready", action="store_true")
    args = parser.parse_args()
    print(json.dumps(state() if args.state else workers_ready() if args.workers_ready else check()))


if __name__ == "__main__":
    main()
