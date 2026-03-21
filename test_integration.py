import requests
import json
import time

API_URL = "http://localhost:8000"
DEVICE_KEY = "test-secret-key"
DEVICE_ID = "550e8400-e29b-41d4-a716-446655440000"

def test_ingest():
    print("Testing Ingest...")

    headers = {
        "Authorization": f"Bearer {DEVICE_KEY}",
        "Content-Type": "application/json"
    }

    # Ingest bioelectric + temp
    payload = {
        "device_id": DEVICE_ID,
        "samples": [
            {
                "species_name": "Tomato",
                "metric_key": "bioelectric_voltage_mv",
                "value": 12.5,
                "timestamp": "2026-02-06T18:00:00Z"
            },
            {
                "species_name": "Tomato",
                "metric_key": "air_temperature_c",
                "value": 24.2,
                "timestamp": "2026-02-06T18:00:00Z"
            },
            {
                 "species_name": "Tomato",
                 "metric_key": "bioelectric_voltage_mv",
                 "value": 15.0,
                 "timestamp": "2026-02-06T18:15:00Z"
            }
        ]
    }

    try:
        resp = requests.post(f"{API_URL}/ingest", json=payload, headers=headers)
        print(f"Ingest Status: {resp.status_code}")
        print(f"Response: {resp.text}")
        if resp.status_code != 201:
             print("FAILED")
             return False
    except Exception as e:
        print(f"Error: {e}")
        return False

    return True

def test_query_latest():
    print("\nTesting Query Latest...")
    # Tomato ID = 2 usually
    try:
        resp = requests.get(f"{API_URL}/species/2/live/latest")
        if resp.status_code != 200:
             # Try ID 3 or 4 if 2 fails (depends on DB seed)
             print(f"Failed ID 2: {resp.status_code}")
             return False

        data = resp.json()
        print(json.dumps(data, indent=2))

        # Check for bioelectric
        found = False
        for m in data['latest']:
            if m['metric_key'] == 'bioelectric_voltage_mv':
                found = True

        if found:
            print("Bioelectric metric found!")
        else:
            print("Bioelectric metric NOT found.")

    except Exception as e:
        print(e)
        return False

    return True

if __name__ == "__main__":
    if test_ingest():
        time.sleep(1)
        test_query_latest()
