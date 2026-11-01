"""Versioned, source-independent signal decoding and quality contract.

This module does not change historical feature records or enable any writer.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import sys

import numpy as np

from app.services.signal_quality_v1 import classify_quality_v1 as classify_quality

CONTRACT_VERSION = "signal-contract-v1"


def canonical_sha256(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def decode_pcm(payload, bits, channels, *, encoding="signed"):
    """Decode interleaved little-endian PCM without altering the source bytes."""
    if bits not in (16, 24) or type(channels) is not int or channels < 1:
        raise ValueError("Unsupported PCM format")
    if encoding not in ("signed", "unsigned"):
        raise ValueError("Unsupported PCM encoding")
    frame_bytes = channels * (bits // 8)
    if not payload or len(payload) % frame_bytes:
        raise ValueError("PCM data must contain complete frames")
    if bits == 16:
        data = np.frombuffer(payload, dtype="<u2").astype(np.int32)
    else:
        octets = np.frombuffer(payload, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        data = octets[:, 0] | (octets[:, 1] << 8) | (octets[:, 2] << 16)
    if encoding == "signed":
        sign = 1 << (bits - 1)
        data = (data ^ sign) - sign
    return data.reshape(-1, channels)


def decode_values(raw, *, bits, encoding, clip_bounds, descriptor):
    """Apply a fully specified calibration descriptor to raw channel values."""
    if bits not in (16, 24) or encoding not in ("signed", "unsigned"):
        raise ValueError("Unsupported encoding")
    if set(descriptor) != {
        "status",
        "scale",
        "offset",
        "unit",
        "source",
        "channel",
        "encoding",
        "validity",
    }:
        raise ValueError("Incomplete calibration descriptor")
    if descriptor["encoding"] != encoding or descriptor["status"] not in {
        "unknown",
        "nominal",
        "declared",
        "verified",
    }:
        raise ValueError("Invalid calibration status or encoding")
    if (
        not isinstance(descriptor["channel"], (str, int))
        or not isinstance(descriptor["validity"], str)
        or not descriptor["validity"]
    ):
        raise ValueError("Calibration binding missing")
    if (
        not isinstance(descriptor["source"], str)
        or not descriptor["source"]
        or not isinstance(descriptor["unit"], str)
        or not descriptor["unit"]
    ):
        raise ValueError("Calibration provenance missing")
    scale, offset = descriptor["scale"], descriptor["offset"]
    if (
        any(
            isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
            for x in (scale, offset)
        )
        or scale == 0
    ):
        raise ValueError("Invalid calibration coefficients")
    if descriptor["status"] == "unknown" and (
        scale != 1 or offset != 0 or descriptor["unit"] != "adc_counts"
    ):
        raise ValueError("Unknown calibration must retain ADC counts")
    low, high = clip_bounds
    minimum = -(1 << (bits - 1)) if encoding == "signed" else 0
    maximum = (1 << (bits - (1 if encoding == "signed" else 0))) - 1
    if not minimum <= low < high <= maximum:
        raise ValueError("Invalid raw clipping bounds")
    raw = np.asarray(raw)
    if (
        raw.ndim != 1
        or not raw.size
        or not np.issubdtype(raw.dtype, np.integer)
        or np.any(raw < minimum)
        or np.any(raw > maximum)
    ):
        raise ValueError("Invalid raw samples")
    values = raw.astype(np.float64) * scale + offset
    bounds = tuple(sorted((low * scale + offset, high * scale + offset)))
    if not np.isfinite(values).all() or not all(math.isfinite(v) for v in bounds):
        raise ValueError("Non-finite calibrated values")
    return {
        "values": values,
        "unit": descriptor["unit"],
        "clipping_bounds": bounds,
        "calibration": dict(descriptor),
        "calibration_sha256": canonical_sha256(descriptor),
    }


def build_signal_payload(
    raw,
    sample_rate,
    *,
    bits,
    encoding,
    clip_bounds,
    descriptor,
    continuity,
    source_quality,
    coverage_ratio,
    observations,
):
    from app.services import wav_feature_service as calculator

    decoded = decode_values(
        raw, bits=bits, encoding=encoding, clip_bounds=clip_bounds, descriptor=descriptor
    )
    features, anomaly_index = calculator.calculate_signal_features(
        decoded["values"], sample_rate, clipping_bounds=decoded["clipping_bounds"]
    )
    quality = classify_quality(features, continuity, source_quality, coverage_ratio, observations)
    unit = decoded["unit"]
    # Hash the complete imported module: helpers, FFT and constants are part of the calculator.
    source_sha = hashlib.sha256(inspect.getsource(calculator).encode()).hexdigest()
    payload = {
        "contract_version": CONTRACT_VERSION,
        "calculator_source_sha256": source_sha,
        "sample_rate_hz": sample_rate,
        "sample_count": int(len(raw)),
        "calibration": decoded["calibration"],
        "calibration_sha256": decoded["calibration_sha256"],
        "raw_clip_bounds": list(clip_bounds),
        "features": features,
        "anomaly_index": anomaly_index,
        "units": {
            "amplitude": unit,
            "spectral_energy": f"{unit}^2",
            "frequency": "Hz",
            "time": "s",
            "ratio": "dimensionless",
        },
        "continuity": dict(continuity),
        "source_quality_counts": dict(source_quality),
        "coverage_ratio": coverage_ratio,
        "quality": quality,
    }
    canonical_sha256(payload)  # Reject non-finite values before returning.
    return payload


def implementation_identity():
    from app.services import signal_quality_v1
    from app.services import wav_feature_service as calculator

    return {
        "contract_version": CONTRACT_VERSION,
        "quality_source_sha256": hashlib.sha256(
            inspect.getsource(signal_quality_v1).encode()
        ).hexdigest(),
        "contract_source_sha256": hashlib.sha256(
            inspect.getsource(sys.modules[__name__]).encode()
        ).hexdigest(),
        "calculator_source_sha256": hashlib.sha256(
            inspect.getsource(calculator).encode()
        ).hexdigest(),
    }


def effective_parameter_sha256(payload):
    """Bind runtime implementation, the calculator and effective clip bounds."""
    from app.services import signal_quality_v1

    return canonical_sha256(
        {
            "contract_version": CONTRACT_VERSION,
            "quality_source_sha256": hashlib.sha256(
                inspect.getsource(signal_quality_v1).encode()
            ).hexdigest(),
            "contract_source_sha256": hashlib.sha256(
                inspect.getsource(sys.modules[__name__]).encode()
            ).hexdigest(),
            "calculator_source_sha256": payload["calculator_source_sha256"],
            "raw_clip_bounds": payload["raw_clip_bounds"],
            "calibration_sha256": payload["calibration_sha256"],
        }
    )
