"""Public capabilities and derived sessions must stay revocable."""

from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.observation import PlantObservationAccess, PlantObservationSession


@pytest.fixture
def public_plant(client, setup_test_data, tenant_admin_token):
    headers = {"Authorization": f"Bearer {tenant_admin_token}"}
    response = client.post(
        "/api/v1/plants",
        json={"name": "Revocable", "zone_id": str(setup_test_data["zone"].id)},
        headers=headers,
    )
    assert response.status_code == 201
    plant_id = response.json()["id"]
    access_url = f"/api/v1/plants/{plant_id}/observation-access"
    response = client.post(access_url, headers=headers)
    assert response.status_code == 200
    access = response.json()
    response = client.post(
        "/api/v1/public/observe/session", json={"public_id": access["public_id"]}
    )
    assert response.status_code == 200
    return headers, access_url, access, response.json()["session_token"]


def test_existing_session_stops_after_revoke(client, db, public_plant):
    headers, access_url, access, token = public_plant
    context_url = f"/api/v1/public/observe/session/{token}/context"
    assert client.get(context_url).status_code == 200
    assert client.delete(access_url, headers=headers).status_code == 200
    assert client.get(context_url).status_code == 401
    assert db.query(PlantObservationSession).filter_by(session_token=token).one().is_active is False


def test_revoke_disables_hidden_duplicate_accesses(client, db, public_plant):
    headers, access_url, access, token = public_plant
    # Simulate a database that has not yet received the controlled upgrade.
    db.execute(text("DROP INDEX IF EXISTS uq_plant_observation_access_active_plant"))
    original = db.query(PlantObservationAccess).filter_by(public_id=access["public_id"]).one()
    duplicate = PlantObservationAccess(plant_id=original.plant_id)
    db.add(duplicate)
    db.commit()
    assert client.delete(access_url, headers=headers).status_code == 200
    assert (
        db.query(PlantObservationAccess)
        .filter_by(plant_id=original.plant_id, is_active=True)
        .count()
        == 0
    )
    assert (
        client.post(
            "/api/v1/public/observe/session", json={"public_id": str(duplicate.public_id)}
        ).status_code
        == 404
    )


def _observation(client, token):
    return client.post(
        f"/api/v1/public/observe/session/{token}/observations",
        json={"wellbeing_score": 4, "plant_condition": "good", "notes": "Synthetic"},
    )


def _evaluation(client, token):
    return client.post(
        f"/api/v1/public/evaluate/session/{token}/evaluations",
        json={
            "overall_score": 4,
            "color_raw": "saturated_green",
            "structure_raw": "firm_taut",
            "growth_raw": "normal",
            "water_raw": "optimal",
            "anomalies_raw": ["none"],
        },
    )


def test_all_public_consumers_stop_and_history_survives(client, db, public_plant, monkeypatch):
    from app.models.evaluation import PlantEvaluation
    from app.models.observation import PlantObservation, PlantObservationPhoto
    from app.services import observation_service

    uploads = []

    class Storage:
        def upload_fileobj(self, *args, **kwargs):
            uploads.append(args[2])

    monkeypatch.setattr(observation_service, "_get_s3_client", lambda: Storage())
    headers, access_url, access, token = public_plant
    observation = _observation(client, token)
    assert observation.status_code == 200
    assert _evaluation(client, token).status_code == 200
    buffer = BytesIO()
    Image.new("RGB", (2, 2)).save(buffer, format="PNG")
    payload = buffer.getvalue()
    photo_url = (
        f"/api/v1/public/observe/session/{token}/observations/{observation.json()['id']}/photos"
    )
    assert (
        client.post(photo_url, files={"file": ("synthetic.png", payload, "image/png")}).status_code
        == 200
    )
    assert len(uploads) == 1
    assert client.delete(access_url, headers=headers).status_code == 200
    assert client.get(f"/api/v1/public/observe/session/{token}/context").status_code == 401
    assert _observation(client, token).status_code == 401
    assert _evaluation(client, token).status_code == 401
    assert (
        client.post(photo_url, files={"file": ("synthetic.png", payload, "image/png")}).status_code
        == 401
    )
    assert len(uploads) == 1
    assert (
        db.query(PlantObservation).count()
        == db.query(PlantEvaluation).count()
        == db.query(PlantObservationPhoto).count()
        == 1
    )


def test_reissue_is_explicit_and_does_not_reactivate_old_session(client, db, public_plant):
    headers, url, old, token = public_plant
    assert client.post(url, headers=headers).json()["public_id"] == old["public_id"]
    assert client.delete(url, headers=headers).status_code == 200
    assert client.delete(url, headers=headers).status_code == 200
    assert client.get(url, headers=headers).json()["is_active"] is False
    assert client.post(url, headers=headers).status_code == 409
    fresh = client.post(url + "?reissue=true", headers=headers)
    assert fresh.status_code == 200 and fresh.json()["public_id"] != old["public_id"]
    assert client.get(f"/api/v1/public/observe/session/{token}/context").status_code == 401
    assert (
        client.post(
            "/api/v1/public/observe/session", json={"public_id": old["public_id"]}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/public/observe/session", json={"public_id": fresh.json()["public_id"]}
        ).status_code
        == 200
    )
    assert db.query(PlantObservationAccess).count() == 2
    assert db.query(PlantObservationAccess).filter_by(is_active=True).count() == 1


@pytest.mark.parametrize(
    "change",
    [
        "inactive_parent",
        "revoked_parent",
        "missing_parent",
        "mismatched_plant",
        "inactive_session",
        "expired_session",
    ],
)
def test_live_parent_and_session_state_are_rechecked(client, db, public_plant, change):
    from app.models.plant import Plant

    headers, url, access, token = public_plant
    parent = db.query(PlantObservationAccess).filter_by(public_id=access["public_id"]).one()
    session = db.query(PlantObservationSession).filter_by(session_token=token).one()
    if change == "inactive_parent":
        parent.is_active = False
    elif change == "revoked_parent":
        parent.revoked_at = datetime.now(UTC)
    elif change == "missing_parent":
        db.delete(parent)
    elif change == "mismatched_plant":
        plant = db.get(Plant, parent.plant_id)
        other = Plant(
            organization_id=plant.organization_id, zone_id=plant.zone_id, name="Different"
        )
        db.add(other)
        db.flush()
        session.plant_id = other.id
    elif change == "inactive_session":
        session.is_active = False
    else:
        session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert client.get(f"/api/v1/public/observe/session/{token}/context").status_code == 401
    assert _observation(client, token).status_code == 401
    assert _evaluation(client, token).status_code == 401


def test_revoke_includes_sessions_of_inactive_parent(client, db, public_plant):
    headers, url, access, token = public_plant
    parent = db.query(PlantObservationAccess).filter_by(public_id=access["public_id"]).one()
    old = PlantObservationAccess(plant_id=parent.plant_id, is_active=False)
    db.add(old)
    db.flush()
    old_session = PlantObservationSession(
        plant_id=parent.plant_id,
        access_id=old.id,
        session_token=str(uuid4()),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    db.add(old_session)
    db.commit()
    assert client.delete(url, headers=headers).status_code == 200
    assert db.query(PlantObservationSession).filter_by(is_active=True).count() == 0


@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_other_tenant_cannot_manage_access(client, db, public_plant, admin_token, method):
    headers, url, access, token = public_plant
    result = getattr(client, method)(url, headers={"Authorization": f"Bearer {admin_token}"})
    assert result.status_code == 404
    assert client.get(f"/api/v1/public/observe/session/{token}/context").status_code == 200


def test_no_implicit_creation_on_read(client, setup_test_data, tenant_admin_token):
    headers = {"Authorization": f"Bearer {tenant_admin_token}"}
    result = client.post(
        "/api/v1/plants",
        json={"name": "No public access", "zone_id": str(setup_test_data["zone"].id)},
        headers=headers,
    )
    url = f"/api/v1/plants/{result.json()['id']}/observation-access"
    assert client.get(url, headers=headers).json() is None
    assert client.get(url, headers=headers).json() is None
    assert client.post(url, headers=headers).status_code == 200


def test_database_rejects_second_active_access(db, public_plant):
    headers, url, access, token = public_plant
    parent = db.query(PlantObservationAccess).filter_by(public_id=access["public_id"]).one()
    db.add(PlantObservationAccess(plant_id=parent.plant_id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_failed_revoke_rolls_back_sessions_and_grants(db, public_plant, monkeypatch):
    from app.models.user import User
    from app.services.plant_service import revoke_observation_access

    headers, url, access, token = public_plant
    user = db.query(User).filter_by(email="tenant-admin@test.com").one()

    def fail():
        raise RuntimeError("Synthetic commit failure")

    monkeypatch.setattr(db, "commit", fail)
    with pytest.raises(RuntimeError):
        revoke_observation_access(db, user, access["plant_id"])
    assert db.query(PlantObservationAccess).filter_by(is_active=True).count() == 1
    assert db.query(PlantObservationSession).filter_by(is_active=True).count() == 1


def test_ambiguous_grants_fail_closed_even_before_schema_preparation(client, db, public_plant):
    headers, url, access, token = public_plant
    db.execute(text("DROP INDEX uq_plant_observation_access_active_plant"))
    parent = db.query(PlantObservationAccess).filter_by(public_id=access["public_id"]).one()
    duplicate = PlantObservationAccess(plant_id=parent.plant_id)
    db.add(duplicate)
    db.commit()
    assert (
        client.post(
            "/api/v1/public/observe/session", json={"public_id": access["public_id"]}
        ).status_code
        == 404
    )
    assert client.get(f"/api/v1/public/observe/session/{token}/context").status_code == 401
    assert _observation(client, token).status_code == 401
    assert _evaluation(client, token).status_code == 401
