import numpy as np
import pytest

from app.direct.features import contract_channel_payloads
from app.services.signal_feature_contract import canonical_sha256, decode_pcm, decode_values
from app.services.wav_feature_service import gateway_contract_payload


def descriptor(scale=3300 / 32767, offset=0, status="nominal"):
    return dict(
        status=status,
        scale=scale,
        offset=offset,
        unit="mV",
        source="legacy encoding",
        channel="CH1",
        encoding="signed",
        validity="all",
    )


def context():
    return dict(
        continuity={"frame_gap_count": 0, "source_dropped_samples_delta": None},
        source_quality=dict.fromkeys(
            ("valid", "lead_off", "rail_high", "rail_low", "jump", "recovery")
        ),
        coverage_ratio=1.0,
        observations=dict(
            coverage=True, continuity=True, source_flags=False, dropped_samples=False
        ),
    )


def test_equal_input_and_context_produce_identical_gateway_and_direct_payload():
    raw = np.array([0, 1, 42, 16384, 32766, 32767] * 32, dtype="<i2")
    config = dict(
        sample_bits=16,
        channels=1,
        sample_rate=380,
        channel_labels=["CH1"],
        calibration_version="unsigned-mv-linear-int16-v1",
    )
    direct = contract_channel_payloads(raw.tobytes(), config, [descriptor()], **context())[0][
        "signal"
    ]
    gateway = gateway_contract_payload(raw, 380, descriptor=descriptor(), **context())
    assert direct == gateway
    assert direct["quality"]["data_quality_status"] == "technical_fault"
    assert direct["units"]["spectral_energy"] == "mV^2"


def test_negative_scale_orders_clipping_bounds_and_scales_spectral_energy():
    raw = np.round(1000 * np.sin(np.arange(4096) * 2 * np.pi * 5 / 380)).astype(np.int32)
    one = gateway_contract_payload(raw, 380, descriptor=descriptor(1, 0, "declared"), **context())
    two = gateway_contract_payload(raw, 380, descriptor=descriptor(-2, 30, "declared"), **context())
    assert two["features"]["spectral_energy_total"] == pytest.approx(
        one["features"]["spectral_energy_total"] * 4, rel=1e-8
    )
    assert two["features"]["dominant_frequency_hz"] == one["features"]["dominant_frequency_hz"]
    values = decode_values(
        raw,
        bits=16,
        encoding="signed",
        clip_bounds=(0, 32767),
        descriptor=descriptor(-2, 30, "declared"),
    )
    assert values["clipping_bounds"] == (-65504, 30)


@pytest.mark.parametrize(
    "bits, values",
    [(16, [-32768, -1, 0, 1, 32767]), (24, [-8388608, -65537, -1, 0, 1, 65537, 8388607])],
)
def test_pcm_extremes_and_alignment(bits, values):
    raw = b"".join(x.to_bytes(bits // 8, "little", signed=True) for x in values)
    assert decode_pcm(raw, bits, 1).flatten().tolist() == values
    with pytest.raises(ValueError):
        decode_pcm(raw + b"x", bits, 1)
    with pytest.raises(ValueError):
        decode_pcm(raw, 32, 1)


def test_unknown_and_known_error_precedence():
    raw = np.arange(1, 401, dtype=np.int32)
    payload = gateway_contract_payload(raw, 380, descriptor=descriptor(), **context())
    assert payload["quality"]["data_quality_status"] == "unknown"
    assert payload["quality"]["biological_candidate"] is None
    changed = context()
    changed["source_quality"]["lead_off"] = 1
    payload = gateway_contract_payload(raw, 380, descriptor=descriptor(), **changed)
    assert payload["quality"]["data_quality_status"] == "technical_fault"
    assert "lead_off" in payload["quality"]["technical_fault_reasons"]
    assert "sequence_gaps" not in payload["quality"]["technical_fault_reasons"]


def test_nonfinite_or_unproven_physical_conversion_rejected():
    with pytest.raises(ValueError):
        canonical_sha256({"value": float("nan")})
    with pytest.raises(ValueError):
        decode_values(
            np.array([1]),
            bits=16,
            encoding="signed",
            clip_bounds=(0, 32767),
            descriptor=descriptor(1, 0, "unknown"),
        )


def test_legacy_and_unknown_calibration_resolution():
    from app.direct.features import resolve_calibration

    config = dict(
        sample_bits=16,
        channels=1,
        channel_labels=["CH1"],
        calibration_version="unsigned-mv-linear-int16-v1",
    )
    legacy = resolve_calibration(config, 0)
    assert legacy["status"] == "nominal" and legacy["scale"] == 3300 / 32767
    config["calibration_version"] = "unproven-version"
    unknown = resolve_calibration(config, 0)
    assert unknown["status"] == "unknown" and unknown["unit"] == "adc_counts"
    assert unknown["scale"] == 1 and unknown["offset"] == 0


def test_writer_is_default_off():
    from app.direct.config import DirectSettings

    assert DirectSettings().feature_results_enabled is False
    assert DirectSettings(feature_results_enabled=True).feature_results_enabled is True


def test_classification_is_independent_of_json_key_order():
    import json

    from app.services.signal_quality_v1 import classify_quality_v1

    flags = dict(valid=1, lead_off=1, rail_high=0, rail_low=0, jump=1, recovery=0)
    features = dict(clipping_ratio=0, flatline_seconds=0, outlier_ratio=0)
    continuity = dict(sequence_gap_count=0, source_dropped_samples_delta=0)
    observed = dict(coverage=True, continuity=True, source_flags=True, dropped_samples=True)
    original = classify_quality_v1(features, continuity, flags, 1, observed)
    after_json = classify_quality_v1(
        features, continuity, json.loads(json.dumps(flags, sort_keys=True)), 1, observed
    )
    assert original == after_json
    assert original["technical_fault_reasons"] == ["lead_off", "jump"]
