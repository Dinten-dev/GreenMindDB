"""24-bit reader adapter; retain the existing numerical feature implementation."""

import numpy as np

from app.services.wav_feature_service import (
    EXTRACTOR_VERSION,
    PARAMETER_HASH,
    calculate_signal_features,
)


def channel_features(payload, config):
    bits = config["sample_bits"]
    if bits == 16:
        samples = np.frombuffer(payload, dtype="<i2").astype(np.int32)
    else:
        octets = np.frombuffer(payload, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        samples = octets[:, 0] | (octets[:, 1] << 8) | (octets[:, 2] << 16)
        samples = (samples ^ 0x800000) - 0x800000
    samples = samples.reshape(-1, config["channels"])
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
