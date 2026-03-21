#!/usr/bin/env python3
"""
Import ALL WAV files from a folder into the database as bioelectric timeseries data.
"""
import wave
import struct
import requests
import os
import glob
from datetime import datetime, timedelta, timezone

# Configuration
API_URL = "http://localhost:8000"
DEVICE_KEY = "test-secret-key"
DEVICE_ID = "550e8400-e29b-41d4-a716-446655440000"
WAV_FOLDER = "/Users/traver/Downloads/wav_files"

def wav_to_samples(wav_path: str):
    """Read WAV file and return mV values."""
    with wave.open(wav_path, 'rb') as wav_file:
        n_channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        n_frames = wav_file.getnframes()
        raw_data = wav_file.readframes(n_frames)

    # Unpack samples
    if sample_width == 1:
        samples = list(struct.unpack(f'{n_frames * n_channels}B', raw_data))
        samples = [s - 128 for s in samples]
        max_val = 127
    elif sample_width == 2:
        samples = list(struct.unpack(f'<{n_frames * n_channels}h', raw_data))
        max_val = 32767
    elif sample_width == 4:
        samples = list(struct.unpack(f'<{n_frames * n_channels}i', raw_data))
        max_val = 2147483647
    else:
        return []

    # If stereo, take first channel
    if n_channels == 2:
        samples = samples[::2]

    # Convert to mV (plant bioelectric range: -50 to +50 mV)
    mv_scale = 50.0 / max_val
    return [s * mv_scale for s in samples]

def ingest_batch(samples_payload, headers):
    """Ingest a batch of samples."""
    payload = {"device_id": DEVICE_ID, "samples": samples_payload}
    try:
        res = requests.post(f"{API_URL}/ingest", headers=headers, json=payload)
        if res.status_code == 201:
            return res.json().get('processed', 0)
    except:
        pass
    return 0

def main():
    print("=" * 60)
    print("Bulk WAV to Timeseries Importer")
    print("=" * 60)

    # Find all WAV files
    wav_files = sorted(glob.glob(os.path.join(WAV_FOLDER, "*.wav")))
    print(f"Found {len(wav_files)} WAV files\n")

    if not wav_files:
        print("No WAV files found!")
        return

    # Collect all samples from all files
    all_mv_values = []
    for wav_path in wav_files:
        mv_values = wav_to_samples(wav_path)
        all_mv_values.extend(mv_values)
        print(f"  {os.path.basename(wav_path)}: {len(mv_values)} samples")

    print(f"\nTotal samples: {len(all_mv_values)}")
    print(f"mV range: {min(all_mv_values):.2f} to {max(all_mv_values):.2f}")

    # Spread samples over 24 hours for nice visualization
    spread_hours = 24
    total_seconds = spread_hours * 3600
    time_step = total_seconds / len(all_mv_values)

    print(f"\nSpreading samples over {spread_hours} hours")
    print(f"Time step: {time_step:.4f} seconds per sample")

    # Generate timestamps
    now = datetime.now(timezone.utc)
    base_time = now - timedelta(hours=spread_hours)

    # Prepare payload
    species_name = "Basil"
    samples_payload = []
    for i, mv in enumerate(all_mv_values):
        ts = base_time + timedelta(seconds=i * time_step)
        samples_payload.append({
            "species_name": species_name,
            "metric_key": "bioelectric_voltage_mv",
            "value": round(mv, 2),
            "timestamp": ts.isoformat()
        })

    print(f"\nIngesting {len(samples_payload)} samples for {species_name}...")

    # Batch ingest
    batch_size = 500
    total_processed = 0
    headers = {
        "Authorization": f"Bearer {DEVICE_KEY}",
        "Content-Type": "application/json"
    }

    for i in range(0, len(samples_payload), batch_size):
        batch = samples_payload[i:i+batch_size]
        processed = ingest_batch(batch, headers)
        total_processed += processed
        if (i // batch_size + 1) % 10 == 0:
            print(f"  Progress: {i + len(batch)}/{len(samples_payload)} samples...")

    print(f"\n{'=' * 60}")
    print(f"SUCCESS: Ingested {total_processed} bioelectric signals for {species_name}")
    print(f"{'=' * 60}")

if __name__ == "__main__":
    main()
