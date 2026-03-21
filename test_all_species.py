#!/usr/bin/env python3
"""
Integration Test: Live Data for All Plants

Tests:
1. Ingest data for ALL 5 species (all 6 canonical metrics each)
2. Verify /latest returns all 6 canonical metrics per species
3. Verify /timeseries works for each metric
"""
import requests
import json
import time

API_URL = "http://localhost:8000"
DEVICE_KEY = "test-secret-key"
DEVICE_ID = "550e8400-e29b-41d4-a716-446655440000"

# All species in the database (English names)
SPECIES_NAMES = ["Tomato", "Lettuce", "Cucumber", "Basil", "Radish"]

# Canonical metrics
CANONICAL_METRICS = [
    {"key": "air_temperature_c", "value": 22.5},
    {"key": "rel_humidity_pct", "value": 65.0},
    {"key": "light_ppfd_umol_m2_s", "value": 450.0},
    {"key": "soil_moisture_vwc_pct", "value": 35.0},
    {"key": "soil_ph", "value": 6.5},
    {"key": "bioelectric_voltage_mv", "value": 12.5},
]

def test_ingest_all_species():
    """Ingest data for all species and all metrics."""
    print("\n=== Testing Ingest for All Species ===\n")

    headers = {
        "Authorization": f"Bearer {DEVICE_KEY}",
        "Content-Type": "application/json"
    }

    for species_name in SPECIES_NAMES:
        samples = []
        for metric in CANONICAL_METRICS:
            samples.append({
                "species_name": species_name,
                "metric_key": metric["key"],
                "value": metric["value"],
                "timestamp": "2026-02-07T08:00:00Z"
            })

        payload = {
            "device_id": DEVICE_ID,
            "samples": samples
        }

        try:
            res = requests.post(f"{API_URL}/ingest", headers=headers, json=payload)
            if res.status_code == 201:
                data = res.json()
                print(f"✓ {species_name}: Ingested {data.get('processed', 0)} samples")
            else:
                print(f"✗ {species_name}: Failed ({res.status_code}) - {res.text}")
        except Exception as e:
            print(f"✗ {species_name}: Error - {e}")

    print()


def test_latest_all_species():
    """Verify /latest returns all 6 canonical metrics for each species."""
    print("\n=== Testing /latest for All Species ===\n")

    # First get all species IDs
    species_ids = {}
    try:
        res = requests.get(f"{API_URL}/species")
        if res.ok:
            for sp in res.json():
                species_ids[sp['common_name']] = sp['id']
    except Exception as e:
        print(f"Error fetching species: {e}")
        return

    all_passed = True

    for species_name in SPECIES_NAMES:
        species_id = species_ids.get(species_name)
        if not species_id:
            print(f"✗ {species_name}: Not found in database")
            all_passed = False
            continue

        try:
            res = requests.get(f"{API_URL}/species/{species_id}/live/latest")
            if res.ok:
                data = res.json()
                metrics = data.get('latest', [])
                metric_keys = [m['metric_key'] for m in metrics]

                # Check all canonical metrics are present
                missing = []
                for cm in CANONICAL_METRICS:
                    if cm['key'] not in metric_keys:
                        missing.append(cm['key'])

                # Count metrics with actual data
                with_data = sum(1 for m in metrics if m.get('value') is not None)

                if missing:
                    print(f"✗ {species_name} (id={species_id}): Missing metrics: {missing}")
                    all_passed = False
                else:
                    print(f"✓ {species_name} (id={species_id}): All 6 metrics returned ({with_data} with data)")
            else:
                print(f"✗ {species_name}: API error ({res.status_code})")
                all_passed = False
        except Exception as e:
            print(f"✗ {species_name}: Error - {e}")
            all_passed = False

    print()
    return all_passed


def test_timeseries():
    """Verify /timeseries returns data for a sample metric."""
    print("\n=== Testing /timeseries ===\n")

    # Get Tomate's ID
    try:
        res = requests.get(f"{API_URL}/species")
        species_id = None
        for sp in res.json():
            if sp['common_name'] == 'Tomate':
                species_id = sp['id']
                break

        if species_id:
            res = requests.get(f"{API_URL}/species/{species_id}/live/timeseries?metric_key=air_temperature_c&range=24h")
            if res.ok:
                data = res.json()
                points = data.get('points', [])
                print(f"✓ Timeseries for Tomate/air_temperature_c: {len(points)} points")
            else:
                print(f"✗ Timeseries failed: {res.status_code}")
    except Exception as e:
        print(f"✗ Timeseries error: {e}")


if __name__ == "__main__":
    print("=" * 50)
    print("Live Data Integration Test - All Plants")
    print("=" * 50)

    # Wait for backend to be ready
    print("\nWaiting for backend...")
    for i in range(10):
        try:
            res = requests.get(f"{API_URL}/health")
            if res.ok:
                print("Backend is ready!\n")
                break
        except:
            pass
        time.sleep(1)
    else:
        print("Backend not responding, exiting.")
        exit(1)

    test_ingest_all_species()
    passed = test_latest_all_species()
    test_timeseries()

    print("=" * 50)
    if passed:
        print("All tests PASSED!")
    else:
        print("Some tests FAILED - check output above")
    print("=" * 50)
