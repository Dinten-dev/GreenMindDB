#!/usr/bin/env python3
"""Import just a few WAV files for Basil - 5 minutes of data."""
import wave
import struct
import requests
import os
import glob
from datetime import datetime, timedelta, timezone

API_URL = "http://localhost:8000"
DEVICE_KEY = "test-secret-key"
DEVICE_ID = "550e8400-e29b-41d4-a716-446655440000"
WAV_FOLDER = "/Users/traver/Downloads/wav_files"

def wav_to_samples(wav_path: str):
    with wave.open(wav_path, 'rb') as f:
        n_ch = f.getnchannels()
        sw = f.getsampwidth()
        n_frames = f.getnframes()
        raw = f.readframes(n_frames)

    if sw == 2:
        samples = list(struct.unpack(f'<{n_frames * n_ch}h', raw))
        max_val = 32767
    else:
        return []

    if n_ch == 2:
        samples = samples[::2]

    return [s * 50.0 / max_val for s in samples]

# Get first 10 WAV files only (about 30 seconds of original data)
wav_files = sorted(glob.glob(os.path.join(WAV_FOLDER, "*.wav")))[:10]
print(f"Using {len(wav_files)} WAV files")

all_mv = []
for f in wav_files:
    all_mv.extend(wav_to_samples(f))

print(f"Total samples: {len(all_mv)}")
print(f"mV range: {min(all_mv):.2f} to {max(all_mv):.2f}")

# Spread over 5 minutes
spread_minutes = 5
now = datetime.now(timezone.utc)
base_time = now - timedelta(minutes=spread_minutes)
time_step = (spread_minutes * 60) / len(all_mv)

samples_payload = []
for i, mv in enumerate(all_mv):
    ts = base_time + timedelta(seconds=i * time_step)
    samples_payload.append({
        "species_name": "Basil",
        "metric_key": "bioelectric_voltage_mv",
        "value": round(mv, 2),
        "timestamp": ts.isoformat()
    })

print(f"\nIngesting {len(samples_payload)} samples for Basil (5 min spread)...")

headers = {"Authorization": f"Bearer {DEVICE_KEY}", "Content-Type": "application/json"}
total = 0
for i in range(0, len(samples_payload), 500):
    batch = samples_payload[i:i+500]
    res = requests.post(f"{API_URL}/ingest", headers=headers, json={"device_id": DEVICE_ID, "samples": batch})
    if res.status_code == 201:
        total += res.json().get('processed', 0)

print(f"SUCCESS: Ingested {total} samples for Basil")
