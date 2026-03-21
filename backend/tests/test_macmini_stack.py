"""Integration tests for the GreenMindDB Mac mini stack.

Tests run against the full Docker Compose stack (db, minio, api, proxy).
"""

from __future__ import annotations

import io
import time
import zipfile
from uuid import uuid4

import httpx
import pytest

GREENHOUSE_ID = "11111111-1111-1111-1111-111111111111"
PLANT_ID = "33333333-3333-3333-3333-333333333333"
PLANT_SENSOR_ID = "55555555-5555-5555-5555-555555555555"
ENV_SENSOR_ID = "66666666-6666-6666-6666-666666666666"

pytestmark = pytest.mark.integration


# ── Auth Tests ────────────────────────────────────────────────


class TestAuth:
    def test_admin_login_returns_tokens(self, base_url, seeded_stack):
        resp = httpx.post(
            f"{base_url}/auth/login",
            json={"email": seeded_stack["ADMIN_EMAIL"], "password": seeded_stack["ADMIN_PASSWORD"]},
            verify=False,
            timeout=10.0,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["role"] == "admin"
        assert data["greenhouse_id"] is None

    def test_operator_login_returns_greenhouse_id(self, base_url, operator_user_and_token):
        # operator was already logged in by the fixture, but let's verify via a second login
        resp = httpx.post(
            f"{base_url}/auth/login",
            json={"email": "operator@test.local", "password": "test-operator-pass"},
            verify=False,
            timeout=10.0,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "operator"
        assert data["greenhouse_id"] == GREENHOUSE_ID

    def test_refresh_token_rotation(self, base_url, operator_user_and_token):
        old_refresh = operator_user_and_token["refresh_token"]
        resp = httpx.post(
            f"{base_url}/auth/refresh",
            json={"refresh_token": old_refresh},
            verify=False,
            timeout=10.0,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["refresh_token"] != old_refresh  # rotated

    def test_invalid_credentials_returns_401(self, base_url):
        resp = httpx.post(
            f"{base_url}/auth/login",
            json={"email": "bad@test.local", "password": "wrong"},
            verify=False,
            timeout=10.0,
        )
        assert resp.status_code == 401

    def test_operator_cannot_access_admin_endpoints(self, base_url, operator_user_and_token):
        token = operator_user_and_token["token"]
        resp = httpx.get(
            f"{base_url}/admin/users",
            headers={"Authorization": f"Bearer {token}"},
            verify=False,
            timeout=10.0,
        )
        assert resp.status_code == 403

    def test_admin_can_list_users(self, base_url, admin_token):
        resp = httpx.get(
            f"{base_url}/admin/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            verify=False,
            timeout=10.0,
        )
        assert resp.status_code == 200
        users = resp.json()
        assert len(users) >= 2  # admin + operator


# ── Ingestion Tests ───────────────────────────────────────────


class TestIngestion:
    def test_ingest_plant_signal_and_query(self, base_url, seeded_stack, admin_token):
        request_id = str(uuid4())
        payload = {
            "plant_id": PLANT_ID,
            "sensor_id": PLANT_SENSOR_ID,
            "greenhouse_id": GREENHOUSE_ID,
            "start_time": "2026-02-14T12:00:00Z",
            "dt_seconds": 1,
            "values_uV": [1203, 1201, 1198],
            "quality": [0, 0, 0],
            "request_id": request_id,
        }

        with httpx.Client(base_url=base_url, timeout=15.0, verify=False) as client:
            ingest_resp = client.post(
                "/v1/ingest/plant-signal-1hz",
                json=payload,
                headers={"Authorization": f"Bearer {seeded_stack['INGEST_TOKEN']}"},
            )
            assert ingest_resp.status_code == 200
            assert ingest_resp.json()["inserted_rows"] == 3
            assert ingest_resp.json()["status"] == "ingested"

            # Query as admin
            query_resp = client.get(
                f"/operator/plants/{PLANT_ID}/signal",
                params={"from": "2026-02-14T11:59:59Z", "to": "2026-02-14T12:00:03Z", "agg": "raw"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert query_resp.status_code == 200
            points = query_resp.json()["points"]
            assert len(points) == 3

    def test_idempotency_same_request_id_is_noop(self, base_url, seeded_stack):
        request_id = str(uuid4())
        payload = {
            "plant_id": PLANT_ID,
            "sensor_id": PLANT_SENSOR_ID,
            "greenhouse_id": GREENHOUSE_ID,
            "start_time": "2026-02-14T13:00:00Z",
            "dt_seconds": 1,
            "values_uV": [1300, 1301],
            "quality": [0, 0],
            "request_id": request_id,
        }
        headers = {"Authorization": f"Bearer {seeded_stack['INGEST_TOKEN']}"}

        with httpx.Client(base_url=base_url, timeout=15.0, verify=False) as client:
            first = client.post("/v1/ingest/plant-signal-1hz", json=payload, headers=headers)
            second = client.post("/v1/ingest/plant-signal-1hz", json=payload, headers=headers)

        assert first.status_code == 200
        assert first.json()["status"] == "ingested"
        assert second.status_code == 200
        assert second.json()["status"] == "duplicate"
        assert second.json()["inserted_rows"] == 0

    def test_ingest_env_measurement(self, base_url, seeded_stack):
        request_id = str(uuid4())
        payload = {
            "request_id": request_id,
            "measurements": [
                {
                    "sensor_id": ENV_SENSOR_ID,
                    "greenhouse_id": GREENHOUSE_ID,
                    "time": "2026-02-14T14:00:00Z",
                    "value": 21.2,
                    "quality": 0,
                },
                {
                    "sensor_id": ENV_SENSOR_ID,
                    "greenhouse_id": GREENHOUSE_ID,
                    "time": "2026-02-14T14:01:00Z",
                    "value": 21.5,
                    "quality": 0,
                },
            ],
        }
        headers = {"Authorization": f"Bearer {seeded_stack['INGEST_TOKEN']}"}

        resp = httpx.post(
            f"{base_url}/v1/ingest/env",
            json=payload,
            headers=headers,
            verify=False,
            timeout=15.0,
        )
        assert resp.status_code == 200
        assert resp.json()["inserted_rows"] == 2


# ── Ground Truth Tests ────────────────────────────────────────


class TestGroundTruth:
    def test_create_and_list_ground_truth(self, base_url, operator_user_and_token):
        token = operator_user_and_token["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Create
        create_resp = httpx.post(
            f"{base_url}/operator/ground-truth/daily",
            json={
                "plant_id": PLANT_ID,
                "date": "2026-02-14",
                "vitality_score": 4,
                "growth_score": 3,
                "pest_score": 5,
                "disease_score": 5,
                "notes": "Looking good today",
            },
            headers=headers,
            verify=False,
            timeout=10.0,
        )
        assert create_resp.status_code == 201
        gt = create_resp.json()
        assert gt["vitality_score"] == 4
        assert gt["greenhouse_id"] == GREENHOUSE_ID

        # List
        list_resp = httpx.get(
            f"{base_url}/operator/ground-truth",
            params={"plant_id": PLANT_ID},
            headers=headers,
            verify=False,
            timeout=10.0,
        )
        assert list_resp.status_code == 200
        items = list_resp.json()
        assert len(items) >= 1
        assert any(i["date"] == "2026-02-14" for i in items)


# ── Annotation Workflow Tests ─────────────────────────────────


class TestAnnotationWorkflow:
    def test_create_submit_and_review_annotation(
        self, base_url, operator_user_and_token, admin_token
    ):
        op_headers = {"Authorization": f"Bearer {operator_user_and_token['token']}"}
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # 1) Create (draft)
        create_resp = httpx.post(
            f"{base_url}/operator/annotations",
            json={
                "plant_id": PLANT_ID,
                "start_time": "2026-02-14T10:00:00Z",
                "end_time": "2026-02-14T11:00:00Z",
                "label_key": "stress_type",
                "label_value": "water_stress",
                "confidence": 80,
                "notes": "Visible wilting",
            },
            headers=op_headers,
            verify=False,
            timeout=10.0,
        )
        assert create_resp.status_code == 201
        ann = create_resp.json()
        ann_id = ann["id"]
        assert ann["status"] == "draft"

        # 2) Submit
        submit_resp = httpx.post(
            f"{base_url}/operator/annotations/{ann_id}/submit",
            headers=op_headers,
            verify=False,
            timeout=10.0,
        )
        assert submit_resp.status_code == 200
        assert submit_resp.json()["status"] == "submitted"

        # 3) Admin lists submitted
        list_resp = httpx.get(
            f"{base_url}/admin/annotations?status=submitted",
            headers=admin_headers,
            verify=False,
            timeout=10.0,
        )
        assert list_resp.status_code == 200
        submitted = list_resp.json()
        assert any(a["id"] == ann_id for a in submitted)

        # 4) Admin reviews (approve)
        review_resp = httpx.post(
            f"{base_url}/admin/annotations/{ann_id}/review",
            json={"decision": "approve", "notes": "Confirmed water stress"},
            headers=admin_headers,
            verify=False,
            timeout=10.0,
        )
        assert review_resp.status_code == 201
        assert review_resp.json()["decision"] == "approve"


# ── Export Tests ──────────────────────────────────────────────


class TestExport:
    def test_export_includes_all_data_types(self, base_url, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        payload = {
            "greenhouse_id": GREENHOUSE_ID,
            "plant_ids": [PLANT_ID],
            "from": "2026-02-14T00:00:00Z",
            "to": "2026-02-15T00:00:00Z",
            "include_env": True,
            "include_events": True,
            "include_ground_truth": True,
            "include_annotations": True,
            "resample": "raw",
        }

        with httpx.Client(base_url=base_url, timeout=30.0, verify=False) as client:
            create_resp = client.post("/v1/exports/dataset", json=payload, headers=headers)
            assert create_resp.status_code == 200
            export_id = create_resp.json()["export_id"]

            # Poll status
            status_payload = None
            for _ in range(20):
                status_resp = client.get(f"/v1/exports/{export_id}/status", headers=headers)
                assert status_resp.status_code == 200
                status_payload = status_resp.json()
                if status_payload["status"] in {"completed", "failed"}:
                    break
                time.sleep(1)

            assert status_payload is not None
            assert status_payload["status"] == "completed", status_payload

            # Download
            download_resp = client.get(f"/v1/exports/{export_id}/download", headers=headers)
            assert download_resp.status_code == 200
            data = download_resp.content

        assert len(data) > 100
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = set(archive.namelist())
        assert "schema.json" in names
        assert "plant_signal.parquet" in names
        assert "ground_truth.parquet" in names
        assert "annotations.parquet" in names


# ── Health Check Tests ────────────────────────────────────────


class TestHealth:
    def test_health_returns_ok(self, base_url):
        resp = httpx.get(f"{base_url}/health", verify=False, timeout=10.0)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["db"] == "ok"
        assert data["minio"] == "ok"
