#!/usr/bin/env python3
"""
Convert WAV file to timeseries bioelectric data and ingest into database.

This script:
1. Reads a WAV file
2. Extracts audio samples
3. Converts samples to mV values (assuming the original data was bioelectric signals)
4. Ingests into TimescaleDB as bioelectric_voltage_mv for the specified species
"""
import wave
import struct
import requests
from datetime import datetime, timedelta, timezone
import sys

# Configuration
API_URL = "http://localhost:8000"
DEVICE_KEY = "test-secret-key"
DEVICE_ID = "550e8400-e29b-41d4-a716-446655440000"

def wav_to_timeseries(wav_path: str, species_name: str = "Basil", spread_hours: float = 1.0):
    """
    Convert WAV file to timeseries data and ingest into database.

    Args:
        wav_path: Path to WAV file
        species_name: Target species name for ingestion
        spread_hours: Spread the samples over this many hours
    """
    print(f"Reading WAV file: {wav_path}")

    with wave.open(wav_path, 'rb') as wav_file:
        n_channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        framerate = wav_file.getframerate()
        n_frames = wav_file.getnframes()

        print(f"  Channels: {n_channels}")
        print(f"  Sample width: {sample_width} bytes")
        print(f"  Frame rate: {framerate} Hz")
        print(f"  Total frames: {n_frames}")
        print(f"  Duration: {n_frames / framerate:.2f} seconds")

        # Read all frames
        raw_data = wav_file.readframes(n_frames)

    # Unpack samples based on sample width
    if sample_width == 1:
        # 8-bit unsigned
        samples = list(struct.unpack(f'{n_frames * n_channels}B', raw_data))
        samples = [s - 128 for s in samples]  # Convert to signed
        max_val = 127
    elif sample_width == 2:
        # 16-bit signed
        samples = list(struct.unpack(f'<{n_frames * n_channels}h', raw_data))
        max_val = 32767
    elif sample_width == 4:
        # 32-bit signed
        samples = list(struct.unpack(f'<{n_frames * n_channels}i', raw_data))
        max_val = 2147483647
    else:
        raise ValueError(f"Unsupported sample width: {sample_width}")

    # If stereo, take only first channel
    if n_channels == 2:
        samples = samples[::2]

    print(f"  Extracted {len(samples)} samples")

    # Convert to mV (normalize to reasonable bioelectric range: -100 to +100 mV)
    # Plant bioelectric signals typically range from -50 to +50 mV
    mv_scale = 50.0 / max_val
    mv_values = [s * mv_scale for s in samples]

    print(f"  mV range: {min(mv_values):.2f} to {max(mv_values):.2f} mV")

    # Spread samples over specified hours for better visualization
    total_seconds = spread_hours * 3600
    time_step = total_seconds / len(mv_values)

    print(f"  Spreading {len(mv_values)} samples over {spread_hours} hour(s)")
    print(f"  Time step: {time_step:.4f} seconds per sample")

    # Generate timestamps (starting from spread_hours ago until now)
    now = datetime.now(timezone.utc)
    base_time = now - timedelta(hours=spread_hours)

    # Prepare samples for ingestion
    samples_payload = []
    for i, mv in enumerate(mv_values):
        ts = base_time + timedelta(seconds=i * time_step)
        samples_payload.append({
            "species_name": species_name,
            "metric_key": "bioelectric_voltage_mv",
            "value": round(mv, 2),
            "timestamp": ts.isoformat()
        })

    print(f"\nIngesting {len(samples_payload)} samples for {species_name}...")

    # Batch ingest (max 1000 samples per request)
    batch_size = 500
    total_processed = 0

    headers = {
        "Authorization": f"Bearer {DEVICE_KEY}",
        "Content-Type": "application/json"
    }

    for i in range(0, len(samples_payload), batch_size):
        batch = samples_payload[i:i+batch_size]
        payload = {
            "device_id": DEVICE_ID,
            "samples": batch
        }

        try:
            res = requests.post(f"{API_URL}/ingest", headers=headers, json=payload)
            if res.status_code == 201:
                data = res.json()
                processed = data.get('processed', 0)
                total_processed += processed
                print(f"  Batch {i//batch_size + 1}: {processed} samples ingested")
            else:
                print(f"  Batch {i//batch_size + 1}: Failed ({res.status_code}) - {res.text}")
        except Exception as e:
            print(f"  Batch {i//batch_size + 1}: Error - {e}")

    print(f"\nTotal ingested: {total_processed} samples")
    return total_processed


if __name__ == "__main__":
    wav_file = "/Users/traver/Library/Mobile Documents/com~apple~CloudDocs/FHNW/GreenMindDB/lamp_off_20250917_122730_928_plant_ad8232_chunk_86_281769.wav"

    print("=" * 60)
    print("WAV to Timeseries Converter")
    print("=" * 60)

    # Spread data over 1 hour for good visualization
    total = wav_to_timeseries(wav_file, species_name="Basil", spread_hours=1.0)

    print("=" * 60)
    if total > 0:
        print(f"SUCCESS: Ingested {total} bioelectric signals for Basil")
    else:
        print("WARNING: No samples were ingested")
    print("=" * 60)
