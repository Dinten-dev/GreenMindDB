"""HPK-derived read-side signal; source PCM and calibration remain unchanged.

Reference: pgloor/biolingo-hpk c3ad7fcdfb86f3321ce263abdabde671da98a7bc.
HPK stores LP(.25), MA(8), mean(4), 100 Hz, rounded int16 mV*10.
Its calculated high-pass is NOT stored. Direct is 380 Hz: a centred Kaiser
polyphase FIR resamples 20/19 before that chain. This is a derived comparison,
not an identical acquisition. Each contiguous run has independent state; edge
extension and first-value initialization avoid inventing a startup from zero.
Timestamp = first sample of each four-sample group; causal filters add delay.
"""

import io
import math
import wave
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import BoundedSemaphore

import numpy as np
from fastapi import HTTPException

VERSION = "hpk-c3ad7fc-380hz-v1"
MAX_FRAMES = 380 * 600
_SLOT = BoundedSemaphore(1)


@contextmanager
def processing_slot():
    if not _SLOT.acquire(blocking=False):
        raise HTTPException(
            503,
            "Signalfilter belegt; bitte erneut versuchen",
            headers={"Retry-After": "5"},
        )
    try:
        yield
    finally:
        _SLOT.release()


def validate(cfg):
    if (
        cfg["sample_rate"],
        cfg["sample_bits"],
        cfg["channels"],
        cfg["calibration_version"],
    ) != (380, 16, 1, "unsigned-mv-linear-int16-v1"):
        raise HTTPException(422, "HPK-Vergleich benötigt 380-Hz-PCM16 in nominalen mV")


def metadata():
    return {
        "processing": VERSION,
        "derived": True,
        "source_sample_rate": 380,
        "sample_rate": 100,
        "unit": "mV",
        "pcm_scale": "int16 = round(mV * 10)",
        "resampling": "20/19 centred Kaiser FIR, 401 taps, beta=5, phase DC normalized, edge extension",
        "filter": "LP alpha=0.25; moving average=8; mean=4; no high-pass",
        "state": "first value at each contiguous run; reset at gaps/segment boundaries",
        "timestamp": "first sample of each 4-sample group",
        "nominal_filter_delay_seconds": 0.020,
        "reference_commit": "c3ad7fcdfb86f3321ce263abdabde671da98a7bc",
    }


def resample_400(values):
    """Bounded polyphase 380 -> 400 Hz; no expanded 20x temporary array."""
    n = len(values) * 20 // 19
    taps = np.sinc(np.arange(-200, 201, dtype=float) / 20) * np.kaiser(401, 5)
    taps *= 20 / taps.sum()
    padded = np.pad(values, (10, 10), mode="edge")
    output = np.empty(n, dtype=float)
    for phase in range(20):
        first = (-phase) % 20
        indexes = np.arange(first, n, 20)
        if not len(indexes):
            continue
        weights = taps[phase::20]
        # Normalize each phase: a finite FIR must not turn the ADC's DC offset
        # into a periodic signal at the phase cycle frequency.
        convolved = np.convolve(padded, weights / weights.sum())
        output[indexes] = convolved[(indexes * 19 + 400) // 20]
    return output


def process_pcm(payload, cfg):
    validate(cfg)
    if len(payload) % 2:
        raise ValueError("Invalid PCM length")
    frames = len(payload) // 2
    if frames > MAX_FRAMES:
        raise HTTPException(413, "HPK-Vergleich ist auf zehn Minuten begrenzt")
    if frames < 4:
        return np.empty(0, dtype="<i2")
    raw = np.frombuffer(payload, dtype="<i2").astype(float) * (3300 / 32767)
    samples = resample_400(raw)
    state = samples[0]
    for i, sample in enumerate(samples):
        state = 0.25 * sample + 0.75 * state
        samples[i] = state
    # HPK's eight-slot box filter, with explicit first-value boundary extension.
    filtered = np.convolve(np.pad(samples, (7, 0), mode="edge"), np.ones(8) / 8, mode="valid")
    count = len(filtered) // 4
    output = filtered[: count * 4].reshape(-1, 4).mean(axis=1) * 10
    # C roundf rounds ties away from zero (numpy.rint would use ties to even).
    rounded = np.copysign(np.floor(np.abs(output) + 0.5), output)
    return np.clip(rounded, -32768, 32767).astype("<i2")


def wav(payload, cfg):
    samples = process_pcm(payload, cfg)
    if not len(samples):
        raise HTTPException(422, "Aufnahme zu kurz für HPK-Vergleich")
    target = io.BytesIO()
    with wave.open(target, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(100)
        writer.writeframes(samples.tobytes())
    return target.getvalue()


def contiguous(runs):
    """Merge packet boundaries; preserve real gaps and reject overlaps."""
    groups = []
    first, end, parts = None, None, []
    total = 0
    for start, payload in runs:
        if len(payload) % 2 or start < 0:
            raise ValueError("Invalid source run")
        if not payload:
            continue
        total += len(payload) // 2
        if total > MAX_FRAMES:
            raise HTTPException(413, "HPK-Vergleich ist auf zehn Minuten begrenzt")
        if end is not None and start < end:
            raise ValueError("Overlapping source runs")
        if end is not None and start != end:
            groups.append((first, b"".join(parts)))
            first, parts = None, []
        if first is None:
            first = start
        parts.append(payload)
        end = start + len(payload) // 2
    if parts:
        groups.append((first, b"".join(parts)))
    return groups


def waveform(runs, cfg, stamp, seconds):
    validate(cfg)
    origin = cfg["session_start_us"] / 1e6
    for first, payload in contiguous(runs):
        start = origin + first / 380
        if start + len(payload) / 760 <= stamp:
            continue
        samples = process_pcm(payload, cfg)
        offset = max(0, math.ceil((stamp - start) * 100 - 1e-6))
        stop = min(len(samples), offset + seconds * 100)
        points = [
            {
                "timestamp": datetime.fromtimestamp(start + i / 100, UTC).isoformat(),
                "value": int(samples[i]) / 10,
            }
            for i in range(offset, stop)
        ]
        if points:
            return metadata() | {"data": points}
    raise HTTPException(404, "Keine Originaldaten für diesen Zeitpunkt")
