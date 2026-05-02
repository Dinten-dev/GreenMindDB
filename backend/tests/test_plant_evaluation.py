"""Tests for the plant evaluation flow and score mapping logic."""

from fastapi.testclient import TestClient

from app.schemas.evaluation import Anomaly, GrowthState, LeafColor, LeafStructure, WaterState
from app.services.evaluation_service import (
    compute_confidence,
    encode_anomalies,
    map_color_score,
    map_growth_score,
    map_structure_score,
    map_water_score,
)

# ── Unit tests for pure mapping functions ────────────────────────────


class TestColorScoreMapping:
    def test_saturated_green(self):
        assert map_color_score(LeafColor.SATURATED_GREEN) == 5

    def test_light_green(self):
        assert map_color_score(LeafColor.LIGHT_GREEN) == 4

    def test_yellowish(self):
        assert map_color_score(LeafColor.YELLOWISH) == 3

    def test_spotted(self):
        assert map_color_score(LeafColor.SPOTTED) == 2

    def test_brown_dead(self):
        assert map_color_score(LeafColor.BROWN_DEAD) == 1


class TestStructureScoreMapping:
    def test_firm_taut(self):
        assert map_structure_score(LeafStructure.FIRM_TAUT) == 5

    def test_slightly_limp(self):
        assert map_structure_score(LeafStructure.SLIGHTLY_LIMP) == 3

    def test_very_limp(self):
        assert map_structure_score(LeafStructure.VERY_LIMP) == 2

    def test_curled_deformed(self):
        assert map_structure_score(LeafStructure.CURLED_DEFORMED) == 1


class TestGrowthScoreMapping:
    def test_strong(self):
        assert map_growth_score(GrowthState.STRONG) == 5

    def test_normal(self):
        assert map_growth_score(GrowthState.NORMAL) == 4

    def test_slow(self):
        assert map_growth_score(GrowthState.SLOW) == 2

    def test_none(self):
        assert map_growth_score(GrowthState.NONE) == 1


class TestWaterScoreMapping:
    def test_too_dry(self):
        assert map_water_score(WaterState.TOO_DRY) == 2

    def test_optimal(self):
        assert map_water_score(WaterState.OPTIMAL) == 5

    def test_too_wet(self):
        assert map_water_score(WaterState.TOO_WET) == 2


class TestAnomalyBitmask:
    def test_none_only(self):
        assert encode_anomalies([Anomaly.NONE]) == 16

    def test_single_spot(self):
        assert encode_anomalies([Anomaly.SPOTS]) == 1

    def test_multiple_anomalies(self):
        result = encode_anomalies([Anomaly.SPOTS, Anomaly.MOLD, Anomaly.PESTS])
        assert result == 1 | 4 | 8  # 13

    def test_all_anomalies(self):
        result = encode_anomalies([Anomaly.SPOTS, Anomaly.HOLES, Anomaly.MOLD, Anomaly.PESTS])
        assert result == 1 | 2 | 4 | 8  # 15


class TestConfidenceScore:
    def test_perfect_alignment(self):
        # overall=5, all subs=5 → perfect confidence
        assert compute_confidence(5, 5, 5, 5, 5) == 1.0

    def test_full_mismatch(self):
        # overall=5, all subs=1 → avg=1, deviation=4
        assert compute_confidence(5, 1, 1, 1, 1) == 0.0

    def test_moderate_mismatch(self):
        # overall=4, avg subs=(5+3+3+3)/4=3.5, deviation=0.5
        result = compute_confidence(4, 5, 3, 3, 3)
        assert 0.8 <= result <= 1.0

    def test_symmetry(self):
        # Same deviation in both directions should yield same confidence
        high = compute_confidence(5, 3, 3, 3, 3)
        low = compute_confidence(1, 3, 3, 3, 3)
        assert high == low


# ── Integration tests ────────────────────────────────────────────────


def test_evaluation_full_flow(client: TestClient, db, admin_token, setup_test_data):
    """Test the complete QR → session → evaluation flow."""
    zone = setup_test_data["zone"]

    # 1. Create plant
    res = client.post(
        "/api/v1/plants",
        json={"name": "Eval Test Plant", "zone_id": str(zone.id)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 201
    plant_id = res.json()["id"]

    # 2. Generate observation access
    res = client.post(
        f"/api/v1/plants/{plant_id}/observation-access",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 200
    public_id = res.json()["public_id"]

    # 3. Create session (login-free)
    res = client.post(
        "/api/v1/public/observe/session",
        json={"public_id": public_id},
    )
    assert res.status_code == 200
    session_token = res.json()["session_token"]

    # 4. Submit evaluation
    res = client.post(
        f"/api/v1/public/evaluate/session/{session_token}/evaluations",
        json={
            "overall_score": 4,
            "color_raw": "saturated_green",
            "structure_raw": "firm_taut",
            "growth_raw": "normal",
            "water_raw": "optimal",
            "anomalies_raw": ["none"],
        },
    )
    assert res.status_code == 200
    data = res.json()

    # Verify mapped scores
    assert data["overall_score"] == 4
    assert data["color_score"] == 5
    assert data["structure_score"] == 5
    assert data["growth_score"] == 4
    assert data["water_score"] == 5
    assert data["anomalies_vector"] == 16  # none bit
    assert data["plant_id"] == plant_id
    assert data["confidence_score"] is not None
    assert 0.0 <= data["confidence_score"] <= 1.0


def test_evaluation_with_bad_scores(client: TestClient, db, admin_token, setup_test_data):
    """Test evaluation with poor plant health."""
    zone = setup_test_data["zone"]

    res = client.post(
        "/api/v1/plants",
        json={"name": "Sick Plant", "zone_id": str(zone.id)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    plant_id = res.json()["id"]

    res = client.post(
        f"/api/v1/plants/{plant_id}/observation-access",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    public_id = res.json()["public_id"]

    res = client.post(
        "/api/v1/public/observe/session",
        json={"public_id": public_id},
    )
    session_token = res.json()["session_token"]

    res = client.post(
        f"/api/v1/public/evaluate/session/{session_token}/evaluations",
        json={
            "overall_score": 1,
            "color_raw": "brown_dead",
            "structure_raw": "curled_deformed",
            "growth_raw": "none",
            "water_raw": "too_dry",
            "anomalies_raw": ["spots", "mold", "pests"],
            "detail_notes": "Plant is severely damaged",
        },
    )
    assert res.status_code == 200
    data = res.json()

    assert data["overall_score"] == 1
    assert data["color_score"] == 1
    assert data["structure_score"] == 1
    assert data["growth_score"] == 1
    assert data["water_score"] == 2
    assert data["anomalies_vector"] == 1 | 4 | 8  # 13


def test_evaluation_invalid_session(client: TestClient):
    """Reject evaluations with invalid session token."""
    res = client.post(
        "/api/v1/public/evaluate/session/invalid_token/evaluations",
        json={
            "overall_score": 3,
            "color_raw": "light_green",
            "structure_raw": "slightly_limp",
            "growth_raw": "normal",
            "water_raw": "optimal",
            "anomalies_raw": ["none"],
        },
    )
    assert res.status_code == 401


def test_evaluation_missing_required_field(client: TestClient, db, admin_token, setup_test_data):
    """Reject evaluation with missing required fields."""
    zone = setup_test_data["zone"]

    res = client.post(
        "/api/v1/plants",
        json={"name": "Validation Plant", "zone_id": str(zone.id)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    plant_id = res.json()["id"]

    res = client.post(
        f"/api/v1/plants/{plant_id}/observation-access",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    public_id = res.json()["public_id"]

    res = client.post(
        "/api/v1/public/observe/session",
        json={"public_id": public_id},
    )
    session_token = res.json()["session_token"]

    # Missing color_raw
    res = client.post(
        f"/api/v1/public/evaluate/session/{session_token}/evaluations",
        json={
            "overall_score": 3,
            "structure_raw": "firm_taut",
            "growth_raw": "normal",
            "water_raw": "optimal",
            "anomalies_raw": ["none"],
        },
    )
    assert res.status_code == 422


def test_evaluation_invalid_overall_score(client: TestClient, db, admin_token, setup_test_data):
    """Reject evaluation with out-of-range overall score."""
    zone = setup_test_data["zone"]

    res = client.post(
        "/api/v1/plants",
        json={"name": "Range Plant", "zone_id": str(zone.id)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    plant_id = res.json()["id"]

    res = client.post(
        f"/api/v1/plants/{plant_id}/observation-access",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    public_id = res.json()["public_id"]

    res = client.post(
        "/api/v1/public/observe/session",
        json={"public_id": public_id},
    )
    session_token = res.json()["session_token"]

    res = client.post(
        f"/api/v1/public/evaluate/session/{session_token}/evaluations",
        json={
            "overall_score": 7,
            "color_raw": "light_green",
            "structure_raw": "firm_taut",
            "growth_raw": "normal",
            "water_raw": "optimal",
            "anomalies_raw": ["none"],
        },
    )
    assert res.status_code == 422


def test_evaluation_anomaly_none_with_others(client: TestClient, db, admin_token, setup_test_data):
    """Reject 'none' combined with other anomalies."""
    zone = setup_test_data["zone"]

    res = client.post(
        "/api/v1/plants",
        json={"name": "Anomaly Plant", "zone_id": str(zone.id)},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    plant_id = res.json()["id"]

    res = client.post(
        f"/api/v1/plants/{plant_id}/observation-access",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    public_id = res.json()["public_id"]

    res = client.post(
        "/api/v1/public/observe/session",
        json={"public_id": public_id},
    )
    session_token = res.json()["session_token"]

    res = client.post(
        f"/api/v1/public/evaluate/session/{session_token}/evaluations",
        json={
            "overall_score": 3,
            "color_raw": "light_green",
            "structure_raw": "firm_taut",
            "growth_raw": "normal",
            "water_raw": "optimal",
            "anomalies_raw": ["none", "spots"],
        },
    )
    assert res.status_code == 422
