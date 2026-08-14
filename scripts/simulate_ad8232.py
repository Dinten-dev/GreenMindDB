#!/usr/bin/env python3
"""
Simulate an ESP32 reading AD8232 bio-signals.
Generates 380Hz data with simulated heartbeats, noise, and jump/rail artifacts.
Ingests the data chunk-by-chunk to the local FastAPI backend.
"""

import math
import random
import time
import requests

API_URL = "http://localhost:8000/api/v1/biosignal/ingest"
MAC_ADDRESS = "AA:BB:CC:DD:EE:FF"
SAMPLE_RATE = 380
BATCH_SIZE = 380  # 1 second batches


def generate_batch(tick: int):
    readings = []
    
    # We'll simulate a 1 Hz heartbeat pulse + baseline + sine drift
    t_start = tick
    for i in range(BATCH_SIZE):
        t = t_start + (i / SAMPLE_RATE)
        
        # 1. Base Signal (Center 1.65V)
        # Add some slow wandering baseline
        baseline = 1650.0 + math.sin(t * 0.5) * 50.0
        
        # Simulated pulse (sharp spike like QRS)
        pulse = 0.0
        if (t % 1.0) < 0.05:
            pulse = math.sin((t % 1.0) * math.pi * 20) * 800.0
            
        mv = baseline + pulse + random.uniform(-10, 10)
        
        lp, lm, flags = 0, 0, 0
        
        # Inject occasional artifacts
        # Minute 0, second 2: Lead off event for 0.5s
        if 2.0 < t < 2.5:
            lp, lm = 1, 1
            mv = 3300.0
            flags |= 1 # Lead off
            flags |= 2 # Rail high
            
        # Second 4: Jump artifact
        if 4.0 < t < 4.1:
            mv += 1000.0
            flags |= 8 # Jump
            
        readings.append([mv, lp, lm, flags])
        
    return {
        "mac_address": MAC_ADDRESS,
        "sample_rate": SAMPLE_RATE,
        "hardware": "AD8232_Sim",
        "columns": ["out_mv", "lo_plus", "lo_minus", "flags"],
        "readings": readings
    }


def run_simulation(duration_seconds=10):
    print(f"Starting AD8232 simulation for {duration_seconds} seconds...")
    session_id = None
    
    for i in range(duration_seconds):
        batch = generate_batch(tick=i)
        
        try:
            r = requests.post(API_URL, json=batch, timeout=2.0)
            if r.status_code == 201:
                session_id = r.json().get("session_id")
                print(f"[{i:02d}] Ingest OK -> Session: {session_id}")
            else:
                print(f"[{i:02d}] Ingest failed: {r.text}")
        except Exception as e:
            print(f"[{i:02d}] Connection error: {e}")
            
        time.sleep(1.0) # Real-time speed
        
    print("\nSimulation complete!")
    if session_id:
        print("\nTriggering WAV export...")
        try:
            r = requests.post(f"http://localhost:8000/api/v1/biosignal/sessions/{session_id}/export-wav")
            print(f"Export reply: {r.text}")
        except Exception as e:
            print(f"Export error: {e}")


if __name__ == "__main__":
    run_simulation(duration_seconds=5)
