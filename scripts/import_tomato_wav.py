#!/usr/bin/env python3
"""
Import TWI Ann Arbor Tomato bioelectric WAV files into the database.

This script reads WAV files from data/wav_examples/tomato/ and imports them
as bioelectric voltage data for the Tomato species.

Usage:
    python scripts/import_tomato_wav.py
"""
import wave
import struct
import requests
import os
import glob
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Configuration
API_URL = os.getenv("API_URL", "http://localhost:8000")
DEVICE_KEY = os.getenv("DEVICE_KEY", "test-secret-key")
DEVICE_ID = os.getenv("DEVICE_ID", "550e8400-e29b-41d4-a716-446655440000")

# Get project root (parent of scripts/)
PROJECT_ROOT = Path(__file__).parent.parent
WAV_FOLDER = PROJECT_ROOT / "data" / "wav_examples" / "tomato"

SPECIES_NAME = "Tomato"
SPREAD_HOURS = 1.0  # Spread samples over this many hours


def wav_to_mv_samples(wav_path: str) -> tuple[list[float], int, float]:
    """
    Read WAV file and convert to mV values.

    Returns:
        (samples_mv, sample_rate, duration_seconds)
    """
    with wave.open(wav_path, 'rb') as f:
        n_channels = f.getnchannels()
        sample_width = f.getsampwidth()
        sample_rate = f.getframerate()
        n_frames = f.getnframes()
        raw_data = f.readframes(n_frames)

    duration = n_frames / sample_rate

    # Unpack based on sample width
    if sample_width == 1:
        samples = list(struct.unpack(f'{n_frames * n_channels}B', raw_data))
        samples = [s - 128 for s in samples]  # Convert unsigned to signed
        max_val = 127
    elif sample_width == 2:
        samples = list(struct.unpack(f'<{n_frames * n_channels}h', raw_data))
        max_val = 32767
    elif sample_width == 4:
        samples = list(struct.unpack(f'<{n_frames * n_channels}i', raw_data))
        max_val = 2147483647
    else:
        raise ValueError(f"Unsupported sample width: {sample_width}")

    # If stereo, take first channel
    if n_channels == 2:
        samples = samples[::2]

    # Convert to mV (bioelectric signals typically -50 to +50 mV)
    mv_scale = 50.0 / max_val
    mv_samples = [s * mv_scale for s in samples]

    return mv_samples, sample_rate, duration


def ingest_samples(samples_payload: list[dict], headers: dict) -> int:
    """Ingest samples in batches, return total processed."""
    batch_size = 500
    total = 0

    for i in range(0, len(samples_payload), batch_size):
        batch = samples_payload[i:i+batch_size]
        payload = {"device_id": DEVICE_ID, "samples": batch}

        try:
            res = requests.post(f"{API_URL}/ingest", headers=headers, json=payload)
            if res.status_code == 201:
                total += res.json().get('processed', 0)
                if (i // batch_size + 1) % 10 == 0:
                    print(f"  Progress: {i + len(batch)}/{len(samples_payload)}")
            else:
                print(f"  Error: {res.status_code} - {res.text[:100]}")
        except Exception as e:
            print(f"  Connection error: {e}")

    return total


def main():
    print("=" * 60)
    print("TWI Ann Arbor Tomato Bioelectric Signal Importer")
    print("=" * 60)

    if not WAV_FOLDER.exists():
        print(f"ERROR: WAV folder not found: {WAV_FOLDER}")
        print("Please place tomato WAV files in data/wav_examples/tomato/")
        return 1

    wav_files = sorted(WAV_FOLDER.glob("*.wav"))
    if not wav_files:
        print(f"No WAV files found in {WAV_FOLDER}")
        return 1

    print(f"Found {len(wav_files)} WAV file(s):\n")

    # Collect all samples from all files
    all_mv = []
    total_duration = 0

    for wav_path in wav_files:
        mv_samples, sample_rate, duration = wav_to_mv_samples(str(wav_path))
        print(f"  {wav_path.name}")
        print(f"    Samples: {len(mv_samples):,}")
        print(f"    Sample rate: {sample_rate} Hz")
        print(f"    Duration: {duration:.2f}s")
        print(f"    mV range: {min(mv_samples):.2f} to {max(mv_samples):.2f}")
        all_mv.extend(mv_samples)
        total_duration += duration

    print(f"\nTotal: {len(all_mv):,} samples, {total_duration:.2f}s original duration")

    # Spread over SPREAD_HOURS for visualization
    total_seconds = SPREAD_HOURS * 3600
    time_step = total_seconds / len(all_mv)

    print(f"Spreading data over {SPREAD_HOURS} hour(s) for visualization")

    # Generate timestamps
    now = datetime.now(timezone.utc)
    base_time = now - timedelta(hours=SPREAD_HOURS)

    # Build payload
    samples_payload = []
    for i, mv in enumerate(all_mv):
        ts = base_time + timedelta(seconds=i * time_step)
        samples_payload.append({
            "species_name": SPECIES_NAME,
            "metric_key": "bioelectric_voltage_mv",
            "value": round(mv, 2),
            "timestamp": ts.isoformat()
        })

    print(f"\nIngesting {len(samples_payload):,} samples for {SPECIES_NAME}...")

    headers = {
        "Authorization": f"Bearer {DEVICE_KEY}",
        "Content-Type": "application/json"
    }

    total = ingest_samples(samples_payload, headers)

    print(f"\n{'=' * 60}")
    if total > 0:
        print(f"SUCCESS: Ingested {total:,} bioelectric signals for {SPECIES_NAME}")
    else:
        print("WARNING: No samples were ingested")
    print("=" * 60)

    return 0 if total > 0 else 1


if __name__ == "__main__":
    exit(main())
