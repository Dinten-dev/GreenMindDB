import requests

url = "http://127.0.0.1:8001/api/v1/biosignal/ingest"
payload = {
    "mac_address": "E2:E2:E2:E2:E2:E2",
    "gateway_serial": "E2E-GATEWAY-1",
    "sample_rate": 380,
    "hardware": "AD8232",
    "columns": ["out_mv", "lp", "lm", "flags"],
    # Create 10 samples, all with flag 1 (invalid)
    "readings": [[1.2, 0, 0, 1] for _ in range(10)]
}

res = requests.post(url, json=payload)
print(res.status_code, res.text)
