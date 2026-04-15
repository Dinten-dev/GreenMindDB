#!/usr/bin/env python3
"""
GreenMind Gateway Simulator
============================
Simulates a Raspberry Pi gateway + ESP32 sensors sending live data to the API.

Usage:
  1. Generate a pairing code in the Dashboard (Gateways → Gateway pairen)
  2. Run:  python scripts/simulate_gateway.py --api-url http://188.245.247.156:8000 --code <PAIRING_CODE>
  3. The simulator registers itself, then starts sending synthetic sensor data every 10s.

First run registers the gateway. Subsequent runs reuse the stored API key from .simulator_state.json.
"""

import argparse
import json
import math
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

STATE_FILE = Path(__file__).parent / ".simulator_state.json"

# Simulated ESP32 sensors
SENSORS = [
    {"mac": "AA:BB:CC:DD:EE:01", "type": "multi"},
    {"mac": "AA:BB:CC:DD:EE:02", "type": "multi"},
]

# Measurement kinds per sensor
SENSOR_KINDS = [
    ("temperature", "°C"),
    ("humidity", "%"),
    ("soil_moisture", "%"),
    ("bioelectric", "mV"),
]

HARDWARE_ID = "RPi-SIM-001"
INTERVAL_SECONDS = 10


def load_state() -> dict | None:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return None


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def register_gateway(api_url: str, code: str) -> dict:
    """Register the simulated gateway using a pairing code."""
    print(f"🔗 Registering gateway with code: {code}")
    resp = requests.post(
        f"{api_url}/api/v1/gateways/register",
        json={
            "code": code,
            "hardware_id": HARDWARE_ID,
            "name": "Demo RPi Simulator",
            "fw_version": "1.0.0-sim",
            "local_ip": "192.168.1.42",
        },
        timeout=10,
    )
    if resp.status_code != 201:
        print(f"❌ Registration failed: {resp.status_code} – {resp.text}")
        sys.exit(1)

    data = resp.json()
    state = {
        "gateway_id": data["gateway_id"],
        "api_key": data["api_key"],
        "greenhouse_id": data["greenhouse_id"],
    }
    save_state(state)
    print(f"✅ Gateway registered: {state['gateway_id']}")
    print(f"   API Key: {state['api_key'][:8]}…")
    return state


def generate_value(kind: str, hour: int, day_progress: float) -> float:
    """Generate a realistic synthetic sensor value."""
    if kind == "temperature":
        base = 23 + 5 * math.sin((hour - 6) * math.pi / 12)
        return round(base + random.gauss(0, 0.3), 2)
    elif kind == "humidity":
        base = 70 - 15 * math.sin((hour - 6) * math.pi / 12)
        return round(max(30, min(95, base + random.gauss(0, 1.5))), 2)
    elif kind == "soil_moisture":
        base = 65 - day_progress * 8  # slowly drains
        return round(max(35, min(85, base + random.gauss(0, 1))), 2)
    elif kind == "bioelectric":
        if 6 <= hour <= 20:
            base = 150 + 100 * math.sin((hour - 6) * math.pi / 14)
        else:
            base = 50
        return round(max(0, base + random.gauss(0, 10)), 1)
    return round(random.uniform(0, 100), 2)


def send_heartbeat(api_url: str, api_key: str):
    """Send a heartbeat to mark the gateway as online."""
    requests.post(
        f"{api_url}/api/v1/gateways/heartbeat",
        json={"hardware_id": HARDWARE_ID, "local_ip": "192.168.1.42"},
        headers={"X-Api-Key": api_key},
        timeout=10,
    )


def send_readings(api_url: str, api_key: str):
    """Generate and send a batch of sensor readings."""
    now = datetime.now(timezone.utc)
    hour = now.hour
    day_progress = (now.hour * 60 + now.minute) / 1440.0

    readings = []
    for sensor in SENSORS:
        for kind, unit in SENSOR_KINDS:
            value = generate_value(kind, hour, day_progress)
            readings.append({
                "sensor_mac": sensor["mac"],
                "sensor_kind": kind,
                "value": value,
                "unit": unit,
                "timestamp": now.isoformat(),
            })

    payload = {
        "measurement_id": str(uuid.uuid4()),
        "gateway_serial": HARDWARE_ID,
        "readings": readings,
    }

    resp = requests.post(
        f"{api_url}/api/v1/ingest",
        json=payload,
        headers={"X-Api-Key": api_key},
        timeout=10,
    )

    if resp.status_code == 201:
        data = resp.json()
        print(
            f"📊 {now.strftime('%H:%M:%S')} | "
            f"Sent {data['ingested']} readings | "
            f"temp={readings[0]['value']}°C  hum={readings[1]['value']}%  "
            f"soil={readings[2]['value']}%  bio={readings[3]['value']}mV"
        )
    else:
        print(f"⚠️  Ingest failed: {resp.status_code} – {resp.text}")


def main():
    parser = argparse.ArgumentParser(description="GreenMind Gateway Simulator")
    parser.add_argument("--api-url", default="http://localhost:8000", help="Backend API URL")
    parser.add_argument("--code", default=None, help="Pairing code (only needed on first run)")
    parser.add_argument("--interval", type=int, default=INTERVAL_SECONDS, help="Seconds between readings")
    parser.add_argument("--reset", action="store_true", help="Delete stored state and re-register")
    args = parser.parse_args()

    if args.reset and STATE_FILE.exists():
        STATE_FILE.unlink()
        print("🗑  State reset")

    state = load_state()

    if not state:
        if not args.code:
            print("❌ No stored state found. Provide a --code to register:")
            print(f"   python {sys.argv[0]} --api-url {args.api_url} --code <PAIRING_CODE>")
            sys.exit(1)
        state = register_gateway(args.api_url, args.code)
    else:
        print(f"♻️  Reusing stored gateway: {state['gateway_id'][:8]}…")

    api_key = state["api_key"]
    heartbeat_counter = 0

    print(f"🚀 Simulator running – sending data every {args.interval}s to {args.api_url}")
    print("   Press Ctrl+C to stop\n")

    try:
        while True:
            send_readings(args.api_url, api_key)

            # Send heartbeat every 6th cycle (~60s)
            heartbeat_counter += 1
            if heartbeat_counter >= 6:
                send_heartbeat(args.api_url, api_key)
                heartbeat_counter = 0

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n\n🛑 Simulator stopped.")


if __name__ == "__main__":
    main()
