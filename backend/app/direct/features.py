"""24-bit reader adapter; retain the existing numerical feature implementation."""

import numpy as np

from app.services.signal_feature_contract import decode_pcm
from app.services.wav_feature_service import (
    EXTRACTOR_VERSION,
    PARAMETER_HASH,
    calculate_signal_features,
)


def channel_features(payload, config):
    bits = config["sample_bits"]
    samples = decode_pcm(payload, bits, config["channels"])
    legacy = config["calibration_version"] == "unsigned-mv-linear-int16-v1"
    if legacy and (bits != 16 or config["channels"] != 1):
        raise ValueError("Legacy calibration requires mono PCM16")
    bounds = (0 if legacy else -(2 ** (bits - 1)), 2 ** (bits - 1) - 1)
    result = []
    for index, label in enumerate(config["channel_labels"]):
        values, _ = calculate_signal_features(
            samples[:, index].astype(np.float64), config["sample_rate"], clipping_bounds=bounds
        )
        result.append(
            {
                "channel": label,
                "unit": "pcm_int16" if legacy else "adc_counts",
                "sample_count": len(samples),
                "extractor_version": EXTRACTOR_VERSION,
                "parameter_hash": PARAMETER_HASH,
                **values,
            }
        )
    return result


def contract_channel_payloads(
    payload, config, descriptors, *, continuity, source_quality, coverage_ratio, observations
):
    """Build new results separately from immutable legacy manifest features."""
    from app.services.signal_feature_contract import build_signal_payload

    bits, channels = config["sample_bits"], config["channels"]
    samples = decode_pcm(payload, bits, channels)
    labels = config["channel_labels"]
    if len(labels) != channels or len(descriptors) != channels:
        raise ValueError("Channel metadata mismatch")
    legacy = config["calibration_version"] == "unsigned-mv-linear-int16-v1"
    if legacy and (bits != 16 or channels != 1):
        raise ValueError("Legacy calibration requires mono PCM16")
    bounds = (0 if legacy else -(2 ** (bits - 1)), 2 ** (bits - 1) - 1)
    results = []
    for index, (label, descriptor) in enumerate(zip(labels, descriptors, strict=True)):
        if descriptor["channel"] != label:
            raise ValueError("Calibration channel mismatch")
        result = build_signal_payload(
            samples[:, index],
            config["sample_rate"],
            bits=bits,
            encoding="signed",
            clip_bounds=bounds,
            descriptor=descriptor,
            continuity=continuity,
            source_quality=source_quality,
            coverage_ratio=coverage_ratio,
            observations=observations,
        )
        results.append({"channel_index": index, "channel_label": label, "signal": result})
    return results


def resolve_calibration(config, channel_index, declared=None):
    """Resolve known encoding only; no coefficients are inferred from version labels."""
    labels = config["channel_labels"]
    if (
        type(channel_index) is not int
        or not 0 <= channel_index < config["channels"]
        or len(labels) != config["channels"]
    ):
        raise ValueError("Invalid channel identity")
    label = labels[channel_index]
    legacy = config["calibration_version"] == "unsigned-mv-linear-int16-v1"
    if legacy and (config["sample_bits"] != 16 or config["channels"] != 1):
        raise ValueError("Legacy calibration requires mono PCM16")
    if declared is not None:
        required = {
            "status",
            "scale",
            "offset",
            "unit",
            "source",
            "channel",
            "encoding",
            "validity",
        }
        if (
            set(declared) != required
            or declared["status"] not in {"declared", "verified"}
            or declared["channel"] != label
            or declared["encoding"] != "signed"
        ):
            raise ValueError("Unbound calibration declaration")
        return dict(declared)
    if legacy:
        return dict(
            status="nominal",
            scale=3300 / 32767,
            offset=0,
            unit="mV",
            source="unsigned-mv-linear-int16-v1",
            channel=label,
            encoding="signed",
            validity="nominal encoding",
        )
    return dict(
        status="unknown",
        scale=1,
        offset=0,
        unit="adc_counts",
        source="unavailable",
        channel=label,
        encoding="signed",
        validity="unknown",
    )
