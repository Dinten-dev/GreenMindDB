import numpy as np

from app.services.signal_feature_contract import (
    build_signal_payload,
    canonical_sha256,
    effective_parameter_sha256,
)


def feature_data():
    descriptor = dict(
        status="unknown",
        scale=1,
        offset=0,
        unit="adc_counts",
        source="unknown",
        channel="CH1",
        encoding="signed",
        validity="unknown",
    )
    observations = dict(coverage=True, continuity=True, source_flags=False, dropped_samples=False)
    continuity = {"frame_gap_count": 0, "source_dropped_samples_delta": None}
    flags = dict.fromkeys(("valid", "lead_off", "rail_high", "rail_low", "jump", "recovery"))
    payload = build_signal_payload(
        np.arange(200),
        380,
        bits=16,
        encoding="signed",
        clip_bounds=(-32768, 32767),
        descriptor=descriptor,
        continuity=continuity,
        source_quality=flags,
        coverage_ratio=1.0,
        observations=observations,
    )
    inputs = dict(
        device_id="d",
        session_id="s",
        segment_id="seg",
        revision=1,
        manifest_sha256="a" * 64,
        wav_key="direct/d/s/test.wav",
        wav_sha256="b" * 64,
        first_frame=0,
        frame_count=200,
        channel_index=0,
        channel_label="CH1",
        extractor_version="signal-contract-v1",
        parameter_sha256=effective_parameter_sha256(payload),
        calibration_sha256=canonical_sha256(descriptor),
        calculator_source_sha256=payload["calculator_source_sha256"],
        quality_context_sha256=canonical_sha256(
            dict(
                continuity=continuity,
                source_quality_counts=flags,
                coverage_ratio=1.0,
                observations=observations,
            )
        ),
    )
    return inputs, payload
