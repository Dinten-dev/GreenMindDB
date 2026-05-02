import uuid
from datetime import UTC, datetime, timedelta
import pytest
from fastapi.testclient import TestClient

def test_public_observation_session_flow(client: TestClient, db, admin_token, setup_test_data):
    # setup_test_data gives us an organization, maybe zones. Let's create a plant.
    zone = setup_test_data["zone"]
    
    # 1. Create plant
    res = client.post(
        "/api/v1/plants",
        json={"name": "Test Plant QR", "zone_id": str(zone.id)},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert res.status_code == 201
    plant_data = res.json()
    plant_id = plant_data["id"]

    # 2. Generate Observation Access (QR public ID)
    res = client.post(
        f"/api/v1/plants/{plant_id}/observation-access",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert res.status_code == 200
    access_data = res.json()
    public_id = access_data["public_id"]
    
    # 3. Create Session (Login-free)
    res = client.post(
        "/api/v1/public/observe/session",
        json={"public_id": public_id}
    )
    assert res.status_code == 200
    session_data = res.json()
    session_token = session_data["session_token"]
    
    # 4. Get Plant Context
    res = client.get(
        f"/api/v1/public/observe/session/{session_token}/context"
    )
    assert res.status_code == 200
    context = res.json()
    assert context["name"] == "Test Plant QR"
    
    # 5. Create Observation
    res = client.post(
        f"/api/v1/public/observe/session/{session_token}/observations",
        json={
            "wellbeing_score": 4,
            "plant_condition": "good",
            "leaf_droop": False,
            "notes": "Testing via pytest"
        }
    )
    assert res.status_code == 200
    obs_data = res.json()
    assert obs_data["wellbeing_score"] == 4
    assert obs_data["notes"] == "Testing via pytest"
    
    # Verify usage count incremented
    res = client.post(
        f"/api/v1/plants/{plant_id}/observation-access",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    access_data = res.json()
    assert access_data["usage_count"] >= 1

def test_invalid_session_fails(client: TestClient):
    res = client.post(
        "/api/v1/public/observe/session/invalid_token/observations",
        json={
            "wellbeing_score": 3,
            "plant_condition": "medium"
        }
    )
    assert res.status_code == 401

def test_revoked_access_fails(client: TestClient, db, admin_token, setup_test_data):
    zone = setup_test_data["zone"]
    
    res = client.post(
        "/api/v1/plants",
        json={"name": "Test Revoked", "zone_id": str(zone.id)},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    plant_id = res.json()["id"]

    res = client.post(
        f"/api/v1/plants/{plant_id}/observation-access",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    public_id = res.json()["public_id"]
    
    # Revoke
    res = client.delete(
        f"/api/v1/plants/{plant_id}/observation-access",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert res.status_code == 200
    
    # Try session
    res = client.post(
        "/api/v1/public/observe/session",
        json={"public_id": public_id}
    )
    assert res.status_code == 404
