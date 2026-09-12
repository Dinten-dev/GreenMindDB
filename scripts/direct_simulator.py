"""Synthetic Direct upload for a provisioned TEST device. Never real sensor data."""

import argparse
import hashlib
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--sample-rate", type=int, default=500)
    parser.add_argument("--channels", type=int, default=4)
    parser.add_argument("--repeat", action="store_true", help="Retry every chunk once to verify idempotency")
    args = parser.parse_args()
    url = urlsplit(args.endpoint)
    if url.scheme != "https" or url.username or url.password or url.query or url.fragment or not url.path.endswith("/api/v1/direct-ingest/chunks"):
        parser.error("Provide the reviewed HTTPS Staging Direct endpoint")
    if not 1 <= args.seconds <= 3600 or not 1 <= args.sample_rate <= 2000 or not 1 <= args.channels <= 8:
        parser.error("Invalid bounded simulation profile")
    token = args.token_file.read_text().strip()
    device_id = str(uuid.UUID(hex=token.split("_")[1]))
    session_id = str(uuid.uuid4())
    # Synthetic history ends now; do not pretend generated data is live ADC data.
    start_us = (int(time.time()) - args.seconds - 1) * 1_000_000
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        for sequence in range(args.seconds):
            first = sequence * args.sample_rate
            payload = b"".join((((first + index) * (channel + 1) % 16_777_216) - 8_388_608).to_bytes(3, "little", signed=True)
                for index in range(args.sample_rate) for channel in range(args.channels))
            metadata = {"protocol_version": 1, "device_id": device_id, "session_id": session_id, "sequence": sequence,
                "session_start_us": start_us, "first_frame": first, "sample_rate": args.sample_rate,
                "channels": args.channels, "sample_bits": 24, "frame_count": args.sample_rate,
                "channel_labels": [f"CH{i + 1}" for i in range(args.channels)], "calibration_version": "synthetic-counts-v1",
                "firmware_version": "simulator-v1", "payload_sha256": hashlib.sha256(payload).hexdigest()}
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream",
                       "X-GreenMind-Metadata": json.dumps(metadata, separators=(",", ":"))}
            for _ in range(2 if args.repeat else 1):
                for attempt in range(8):
                    response = client.post(args.endpoint, headers=headers, content=payload)
                    if response.status_code not in (429, 500, 502, 503, 504):
                        break
                    time.sleep(min(30, 2**attempt))
                response.raise_for_status()
                ack = response.json()
                if ack.get("session_id") != session_id or ack.get("sequence") != sequence or ack.get("payload_sha256") != metadata["payload_sha256"]:
                    raise RuntimeError("Acknowledgement identity mismatch")
    print(f"Verified {args.seconds} synthetic chunks; device={device_id} session={session_id}")


if __name__ == "__main__":
    main()
