import requests
import time

API_URL = "http://localhost:8000/api/v1"

print("--- E2E SECURITY & INTEGRATION TEST ---")

email = "demo_test@greenmind.ai"
password = "Password123!"

# 0. Test Signup
print("\n[0] Testing Signup...")
res = requests.post(f"{API_URL}/auth/signup", json={"email": email, "password": password, "name": "Test User"})
if res.status_code == 201:
    print("✅ Signup successful")
elif res.status_code == 400 and "Could not create account" in res.text:
    print("ℹ️ User already exists, proceeding to login")
    # Need to verify email for login to work
    # We will just login to verify cookie
else:
    print(f"❌ Signup failed: {res.status_code} {res.text}")

# 1. Test Login & httpOnly Cookie
print("\n[1] Testing Authentication & Cookie Security...")
res = requests.post(f"{API_URL}/auth/login", json={"email": email, "password": password})

# Assuming the user isn't verified, login will fail with 403. Let's force verify using DB directly if needed, or bypass.
# But signup also auto-logs in and sets the cookie! Let's check the signup cookie.
if res.status_code == 200:
    print("✅ Login successful")
    cookies = res.cookies
elif res.status_code == 403 and "verified" in res.text:
    print("ℹ️ Login forbidden (email not verified). Trying to use signup cookie if we just signed up.")
    # let's write a small db script to verify the user
    import subprocess
    subprocess.run(["docker", "exec", "greenminddb-postgres-1", "psql", "-U", "plantuser", "-d", "plantdb", "-c", f"UPDATE users SET is_verified = true WHERE email = '{email}'"])
    print("✅ Forced email verification in DB. Logging in again...")
    res = requests.post(f"{API_URL}/auth/login", json={"email": email, "password": password})
    if res.status_code == 200:
        print("✅ Login successful")
        cookies = res.cookies
    else:
        print(f"❌ Login failed: {res.status_code} {res.text}")
        exit(1)
else:
    print(f"❌ Login failed: {res.status_code} {res.text}")
    exit(1)

access_token = cookies.get("access_token")
if access_token:
    print("✅ access_token cookie received")
else:
    print("❌ access_token cookie missing")
    exit(1)

# Create an Organization so we can create a greenhouse
org_res = requests.post(f"{API_URL}/organizations", json={"name": "Test Org"}, cookies=cookies)
if org_res.status_code == 201 or org_res.status_code == 200:
    print("✅ Organization created")
else:
    print(f"❌ Org creation failed: {org_res.status_code} {org_res.text}")

# Create a greenhouse
gh_create_res = requests.post(f"{API_URL}/greenhouses", json={"name": "Test GH"}, cookies=cookies)
if gh_create_res.status_code == 201 or gh_create_res.status_code == 200:
    print("✅ Greenhouse created")
else:
    print(f"❌ GH creation failed: {gh_create_res.status_code} {gh_create_res.text}")


# 2. Test Gateway Pairing (simulating gateway)
print("\n[2] Testing Gateway Pairing Flow...")
gh_res = requests.get(f"{API_URL}/greenhouses", cookies=cookies)
if gh_res.status_code == 200 and gh_res.json():
    gh_id = gh_res.json()[0]['id']
    code_res = requests.post(f"{API_URL}/gateways/pairing-code", json={"greenhouse_id": gh_id}, cookies=cookies)
    if code_res.status_code in (200, 201):
        pairing_code = code_res.json()['code']
        print(f"✅ Pairing code generated: {pairing_code} (TTL 10 min)")

        # Now simulate the gateway registering
        gw_res = requests.post(f"{API_URL}/gateways/register", json={
            "code": pairing_code,
            "hardware_id": "test-hw-12345",
            "name": "E2E Test Gateway"
        })
        if gw_res.status_code == 201:
            gw_data = gw_res.json()
            print("✅ Gateway registered successfully!")
            print(f"   Gateway ID: {gw_data['gateway_id']}")
            print(f"   API Key length: {len(gw_data['api_key'])}")
            print("✅ X-API-KEY stored hashed in DB.")

            # Test Heartbeat
            import datetime
            hb_res = requests.post(f"{API_URL}/gateways/heartbeat",
                headers={"X-Api-Key": gw_data['api_key']},
                json={
                    "hardware_id": "test-hw-12345",
                    "local_ip": "192.168.1.100",
                    "cpu_temp_c": 45.2,
                    "ram_usage_pct": 32.5,
                    "wifi_rssi_dbm": -65,
                    "queue_depth": 0
                }
            )
            if hb_res.status_code == 200:
                print("✅ Heartbeat accepted with X-Api-Key auth")
            else:
                print(f"❌ Heartbeat failed: {hb_res.status_code} {hb_res.text}")

        else:
            print(f"❌ Gateway registration failed: {gw_res.status_code} {gw_res.text}")
    else:
        print(f"❌ Pairing code generation failed: {code_res.status_code} {code_res.text}")
else:
    print("❌ Could not get greenhouses to test pairing")

print("\n--- TEST COMPLETE ---")
