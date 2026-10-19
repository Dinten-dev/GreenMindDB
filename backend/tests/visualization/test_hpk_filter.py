import hashlib
import io
import wave
from datetime import datetime

import numpy as np
import pytest
from fastapi import HTTPException

from app.visualization import hpk_filter as hpk


def config():
    return {
        "sample_rate": 380,
        "sample_bits": 16,
        "channels": 1,
        "calibration_version": "unsigned-mv-linear-int16-v1",
        "session_start_us": 0,
    }


def pcm(values):
    return np.rint(np.asarray(values) * 32767 / 3300).astype("<i2").tobytes()


@pytest.mark.parametrize("frequency", [50, 100, 150])
def test_mains_rejected_before_decimation(frequency):
    t = np.arange(380 * 20) / 380
    values = 1500 + 100 * np.sin(2 * np.pi * frequency * t)
    output = hpk.process_pcm(pcm(values), config())[100:-100] / 10
    assert np.std(output) < 0.15


def test_low_frequency_preserved_and_original_immutable():
    t = np.arange(380 * 20) / 380
    source = pcm(1500 + 100 * np.sin(2 * np.pi * 10 * t))
    before = hashlib.sha256(source).hexdigest()
    samples = hpk.process_pcm(source, config())
    gain = np.std(samples[100:-100] / 10) / (100 / np.sqrt(2))
    assert 0.70 < gain < 0.85
    assert len(samples) == 2000
    assert hashlib.sha256(source).hexdigest() == before


def test_wav_units_rate_duration_and_dc():
    payload = hpk.wav(pcm(np.full(380, 1000)), config())
    with wave.open(io.BytesIO(payload)) as reader:
        assert (
            reader.getframerate(),
            reader.getsampwidth(),
            reader.getnchannels(),
            reader.getnframes(),
        ) == (100, 2, 1, 100)
        samples = np.frombuffer(reader.readframes(100), dtype="<i2")
    assert np.max(np.abs(samples / 10 - 1000)) < 0.2


@pytest.mark.parametrize(
    "key,value",
    [
        ("sample_rate", 400),
        ("sample_bits", 24),
        ("channels", 2),
        ("calibration_version", "raw-adc-counts-v1"),
    ],
)
def test_unknown_calibration_rejected(key, value):
    cfg = config() | {key: value}
    with pytest.raises(HTTPException) as error:
        hpk.wav(b"\0" * 760, cfg)
    assert error.value.status_code == 422


def test_lengths_limits_and_clipping():
    with pytest.raises(ValueError):
        hpk.process_pcm(b"x", config())
    with pytest.raises(HTTPException) as error:
        hpk.process_pcm(b"\0" * (hpk.MAX_FRAMES * 2 + 2), config())
    assert error.value.status_code == 413
    with pytest.raises(HTTPException) as error:
        hpk.wav(b"\0" * 6, config())
    assert error.value.status_code == 422
    assert hpk.process_pcm(np.full(380, 32767, dtype="<i2").tobytes(), config()).max() == 32767


def test_packets_merge_but_gaps_do_not_get_filled():
    source = pcm(1500 + np.sin(np.arange(760) / 10))
    complete = hpk.waveform([(0, source)], config(), 0.3, 1)
    split = hpk.waveform([(0, source[:302]), (151, source[302:])], config(), 0.3, 1)
    assert complete == split
    gap = hpk.waveform([(0, source[:760]), (760, source[:760])], config(), 0.5, 3)
    assert len(gap["data"]) == 50
    assert datetime.fromisoformat(gap["data"][-1]["timestamp"]).timestamp() == 0.99
    with pytest.raises(ValueError, match="Overlapping"):
        hpk.contiguous([(0, source[:760]), (379, source[:760])])


def test_window_uses_full_run_history():
    source = pcm(1500 + 100 * np.sin(np.arange(380 * 5) / 10))
    full = hpk.waveform([(0, source)], config(), 0, 5)
    window = hpk.waveform([(0, source)], config(), 2, 1)
    assert window["data"] == full["data"][200:300]
    assert window["derived"] and "no high-pass" in window["filter"]


def test_concurrent_filters_fail_closed_and_slot_recovers():
    with hpk.processing_slot():
        with pytest.raises(HTTPException) as error:
            with hpk.processing_slot():
                pass
        assert error.value.status_code == 503
    with pytest.raises(ValueError):
        with hpk.processing_slot():
            raise ValueError("test interruption")
    with hpk.processing_slot():
        pass
