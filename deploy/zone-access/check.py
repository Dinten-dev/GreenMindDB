"""Bounded HTTP acceptance. Ephemeral test member, removed even on failure.

Tokens remain in subprocess memory and are never written to reports/logs.
No synthetic readings, WAVs or real-account permissions are created/changed.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import uuid


def inside(container, code):
    result = subprocess.run(
        ["docker", "exec", "-i", container, "python", "-"],
        input=code,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError("Private database check failed; output withheld")
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--public", action="store_true")
    args = parser.parse_args()
    root = args.directory
    m = json.loads((root / "manifest.json").read_text())
    container = m["project"] + "-application-1"
    uid = str(uuid.uuid4())
    domain = (
        "green-mind.ch" if m["environment"] == "production" else "test.green-mind.ch"
    )
    api = (
        "https://" + domain if args.public else "http://127.0.0.1:" + str(m["api_port"])
    )
    visual = api if args.public else "http://127.0.0.1:" + str(m["api_port"] + 1)
    direct = api if args.public else "http://127.0.0.1:" + str(m["api_port"] + 2)
    seed = f"""
from app.database import SessionLocal
from app.models.user import User,Role
from app.models.master import Zone,Gateway,Sensor
from app.auth import create_access_token
from datetime import timedelta
import uuid,json
with SessionLocal() as db:
 admin=db.query(User).filter(User.is_active.is_(True),User.is_verified.is_(True),User.role.in_([Role.OWNER,Role.ADMIN]),User.organization_id.isnot(None)).order_by(User.created_at).first()
 assert admin
 zones=db.query(Zone).filter_by(organization_id=admin.organization_id).order_by(Zone.id).all()
 assert len(zones)>=2
 user=User(id=uuid.UUID('{uid}'),email='rollout-{uid}@example.invalid',password_hash='disabled-login',role=Role.MEMBER,organization_id=admin.organization_id,is_active=True,is_verified=True)
 db.add(user);db.commit()
 sensors=db.query(Sensor.id,Gateway.zone_id).join(Gateway).filter(Gateway.zone_id.in_([z.id for z in zones])).all()
 print(json.dumps({{'admin':create_access_token({{'sub':str(admin.id)}},expires_delta=timedelta(minutes=5)), 'member':create_access_token({{'sub':str(user.id)}},expires_delta=timedelta(minutes=5)), 'zones':[str(z.id) for z in zones], 'sensors':[{{'id':str(s.id),'zone':str(s.zone_id)}} for s in sensors]}}))
"""
    cleanup = f"""
from app.database import SessionLocal
from app.models.user import User
from app.models.zone_access import ZoneAccess
import uuid,json
with SessionLocal() as db:
 db.query(ZoneAccess).filter_by(user_id=uuid.UUID('{uid}')).delete()
 db.query(User).filter_by(id=uuid.UUID('{uid}'),email='rollout-{uid}@example.invalid').delete()
 db.commit()
 print(json.dumps({{'cleaned':True}}))
"""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise RuntimeError("Unexpected HTTP redirect")

    opener = urllib.request.build_opener(NoRedirect)

    def request(base, path, token=None, method="GET", data=None, cookie=False):
        headers = {"X-Forwarded-Proto": "https"}
        if token:
            headers["Cookie" if cookie else "Authorization"] = (
                "access_token=" if cookie else "Bearer "
            ) + token
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            base + path,
            headers=headers,
            method=method,
            data=json.dumps(data).encode() if data is not None else None,
        )
        try:
            with opener.open(req, timeout=20) as response:
                raw = response.read(8000000)
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            return exc.code, None

    checks = []
    try:
        data = inside(container, seed)
        admin = data["admin"]
        member = data["member"]
        assert request(api, "/api/v1/zones")[0] == 401
        assert request(api, "/api/v1/auth/me", admin, cookie=True)[0] == 200
        assert request(api, "/api/v1/zones", member) == (200, [])
        assert request(api, "/api/v1/sensors", member) == (200, [])
        assert request(direct, "/api/v1/direct-ingest/devices", member) == (200, [])
        checks += ["authentication_cookie", "member_no_grants"]
        hidden = data["zones"][-1]
        for sensor in [s for s in data["sensors"] if s["zone"] == hidden][:1]:
            for suffix in ("data?range=5m", "export?range=1h"):
                assert (
                    request(
                        visual, "/api/v1/sensors/" + sensor["id"] + "/" + suffix, member
                    )[0]
                    == 404
                )
            assert (
                request(api, "/api/v1/wav/count?sensor_id=" + sensor["id"], member)[1][
                    "count"
                ]
                == 0
            )
        checks.append("hidden_measurements_exports_recordings")
        selected = data["zones"][:2]
        route = "/api/v1/organizations/members/" + uid + "/zones"
        assert request(api, route, member, "PUT", {"zone_ids": selected})[0] == 403
        assert request(api, route, admin, "PUT", {"zone_ids": selected})[0] == 200
        assert {z["id"] for z in request(api, "/api/v1/zones", member)[1]} == set(
            selected
        )
        sensors = request(api, "/api/v1/sensors", member)[1]
        assert all(s["zone_id"] in selected for s in sensors)
        devices = request(direct, "/api/v1/direct-ingest/devices", member)
        assert devices[0] == 200 and all(d["zone_id"] in selected for d in devices[1])
        checks.append("two_zone_grants_gateway_and_direct")
        assert request(api, route, admin, "PUT", {"zone_ids": []})[0] == 200
        assert request(api, "/api/v1/sensors", member) == (200, [])
        checks.append("revocation")
        admin_devices = request(direct, "/api/v1/direct-ingest/devices", admin)
        assert admin_devices[0] == 200
        readings = 0
        for sensor in data["sensors"]:
            status, series = request(
                visual, "/api/v1/sensors/" + sensor["id"] + "/data?range=5m", admin
            )
            assert status == 200
            readings += sum(len(row["data"]) for row in series)
        checks.append("authenticated_gateway_history")
        direct_points = 0
        for device in admin_devices[1]:
            path = "/api/v1/visualization/direct/" + device["id"] + "/data?range=24h"
            status, series = request(visual, path, admin)
            assert status == 200
            direct_points += sum(len(row["data"]) for row in series)
        checks.append("authenticated_direct_history")
        report = {
            "passed": True,
            "at": time.time(),
            "revision": m["revision"],
            "public": args.public,
            "checks": checks,
            "gateway_points_5m": readings,
            "direct_points_24h": direct_points,
            "direct_devices": len(admin_devices[1]),
        }
    finally:
        assert inside(container, cleanup)["cleaned"]
    target = root / (
        "public-zone-acceptance.json" if args.public else "acceptance.json"
    )
    target.write_text(json.dumps(report, indent=2) + "\n")
    target.chmod(0o600)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
