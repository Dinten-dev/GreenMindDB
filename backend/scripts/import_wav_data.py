#!/usr/bin/env python3
"""
Import WAV files from /data/wav into the database.
Used by Docker entrypoint for automatic data loading.
"""

import glob
import os
import struct
import wave
from datetime import UTC, datetime, timedelta

import psycopg2
from psycopg2.extras import execute_values

# Configuration from environment
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://plantuser:plantpass@postgres:5432/plantdb"
)
WAV_FOLDER = os.environ.get("WAV_FOLDER", "/data/wav")
SPECIES_NAME = os.environ.get("DEFAULT_SPECIES", "Basil")


def get_db_connection():
    """Get database connection."""
    return psycopg2.connect(DATABASE_URL)


def wav_to_samples(wav_path: str):
    """Read WAV file and return mV values."""
    with wave.open(wav_path, "rb") as wav_file:
        n_channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        n_frames = wav_file.getnframes()
        sample_rate = wav_file.getframerate()
        raw_data = wav_file.readframes(n_frames)

    # Unpack samples
    if sample_width == 1:
        samples = list(struct.unpack(f"{n_frames * n_channels}B", raw_data))
        samples = [s - 128 for s in samples]
        max_val = 127
    elif sample_width == 2:
        samples = list(struct.unpack(f"<{n_frames * n_channels}h", raw_data))
        max_val = 32767
    elif sample_width == 4:
        samples = list(struct.unpack(f"<{n_frames * n_channels}i", raw_data))
        max_val = 2147483647
    else:
        return [], sample_rate

    # If stereo, take first channel
    if n_channels == 2:
        samples = samples[::2]

    # Convert to mV (plant bioelectric range: -50 to +50 mV)
    mv_scale = 50.0 / max_val
    return [s * mv_scale for s in samples], sample_rate


def get_or_create_channel(conn, species_id: int, metric_id: int, device_id: str):
    """Get or create telemetry channel."""
    with conn.cursor() as cur:
        # Try to find existing
        cur.execute(
            """
            SELECT id FROM telemetry_channel
            WHERE species_id = %s AND metric_id = %s
        """,
            (species_id, metric_id),
        )
        row = cur.fetchone()

        if row:
            return row[0]

        # Create new
        import uuid

        channel_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO telemetry_channel (id, species_id, metric_id, device_id, channel_key, unit)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """,
            (channel_id, species_id, metric_id, device_id, f"bioelectric_{species_id}", "mV"),
        )
        conn.commit()
        return channel_id


def main():
    print("=" * 60)
    print("WAV to Database Importer (Docker)")
    print("=" * 60)

    # Find WAV files
    wav_files = sorted(glob.glob(os.path.join(WAV_FOLDER, "*.wav")))
    print(f"Found {len(wav_files)} WAV files in {WAV_FOLDER}")

    if not wav_files:
        print("No WAV files to import.")
        return

    conn = get_db_connection()

    try:
        with conn.cursor() as cur:
            # Get species ID
            cur.execute(
                "SELECT id FROM species WHERE lower(common_name) = lower(%s)", (SPECIES_NAME,)
            )
            row = cur.fetchone()
            if not row:
                print(f"Species '{SPECIES_NAME}' not found!")
                return
            species_id = row[0]

            # Get bioelectric metric ID
            cur.execute("SELECT id FROM metric WHERE key = 'bioelectric_voltage_mv'")
            row = cur.fetchone()
            if not row:
                print("Metric 'bioelectric_voltage_mv' not found!")
                return
            metric_id = row[0]

            # Get or create device
            cur.execute("SELECT id FROM device LIMIT 1")
            row = cur.fetchone()
            if not row:
                print("No device found!")
                return
            device_id = str(row[0])

        # Get or create channel
        channel_id = get_or_create_channel(conn, species_id, metric_id, device_id)

        # Collect all samples
        all_samples = []
        for wav_path in wav_files:
            samples, sample_rate = wav_to_samples(wav_path)
            all_samples.extend(samples)
            print(f"  {os.path.basename(wav_path)}: {len(samples)} samples @ {sample_rate}Hz")

        print(f"\nTotal samples: {len(all_samples)}")

        # Spread over 24 hours
        now = datetime.now(UTC)
        base_time = now - timedelta(hours=24)
        time_step = (24 * 3600) / len(all_samples)

        # Prepare data for bulk insert
        measurements = []
        for i, mv in enumerate(all_samples):
            ts = base_time + timedelta(seconds=i * time_step)
            measurements.append((ts, channel_id, round(mv, 2), 0))

        # Bulk insert
        print(f"\nInserting {len(measurements)} measurements...")
        with conn.cursor() as cur:
            execute_values(
                cur,
                "INSERT INTO telemetry_measurement (time, channel_id, value, quality) VALUES %s ON CONFLICT DO NOTHING",
                measurements,
                page_size=1000,
            )
        conn.commit()

        print("=" * 60)
        print(f"SUCCESS: Imported {len(measurements)} bioelectric samples")
        print("=" * 60)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
