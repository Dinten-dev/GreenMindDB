"""Sample comparison using the actual unchanged Gateway writer and C++ codec."""

import io
import os
import sys
import time
import wave
from pathlib import Path

import pytest

from app.direct.assembler import assemble_one
from tests.direct.test_pipeline import current_manifest


@pytest.mark.integration
def test_cpp_direct_output_matches_unchanged_gateway_for_all_deci_mv(
    pipeline, monkeypatch, tmp_path
):
    gateway_source = os.environ.get("DIRECT_GATEWAY_SOURCE")
    cpp_fixture = os.environ.get("DIRECT_CPP_PCM_FIXTURE")
    if not gateway_source or not cpp_fixture:
        pytest.skip("Gateway source and native C++ PCM fixture required")
    sys.path.insert(0, gateway_source)
    from src.config import settings
    from src.runtime import wav_writer

    monkeypatch.setattr(settings, "wav_dir", str(tmp_path / "gateway"))
    monkeypatch.setattr(settings, "wav_min_free_bytes", 0)
    monkeypatch.setattr(wav_writer, "_get_cached_ntp", lambda: True)
    values = [value * 0.1 for value in range(33001)]
    p = pipeline
    for offset in range(0, len(values), 380):
        batch = values[offset : offset + 380]
        wav_writer.write_samples(
            "AA:BB:CC:DD:EE:FF",
            batch,
            380,
            captured_at_epoch_ms=int(p.start_us / 1000 + (offset + len(batch)) * 1000 / 380),
        )
    paths = wav_writer.close_all()
    gateway_pcm = b""
    for path in sorted(paths):
        with wave.open(path, "rb") as source:
            gateway_pcm += source.readframes(source.getnframes())
    cpp_pcm = Path(cpp_fixture).read_bytes()
    assert len(cpp_pcm) == len(gateway_pcm) == 33001 * 2
    assert cpp_pcm == gateway_pcm
    for sequence, offset in enumerate(range(0, 33001, 3800)):
        payload = cpp_pcm[offset * 2 : (offset + 3800) * 2]
        meta = p.metadata(
            payload,
            sample_bits=16,
            channels=1,
            channel_labels=["CH1"],
            sample_rate=380,
            sequence=sequence,
            first_frame=offset,
            frame_count=len(payload) // 2,
            calibration_version="unsigned-mv-linear-int16-v1",
        )
        assert p.upload(payload, meta).status_code == 201
    assert assemble_one(p.engine, p.cfg, p.store, now=time.time() + 2)
    manifest = current_manifest(p)
    with wave.open(io.BytesIO(p.store.get(manifest["runs"][0]["key"])), "rb") as source:
        assert source.readframes(33001) == gateway_pcm
