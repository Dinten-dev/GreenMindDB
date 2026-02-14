#!/usr/bin/env python3
"""
Generate realistic sample data for ALL plants and ALL metrics.
Creates 24 hours of data with natural-looking curves.
"""
import requests
import math
import random
from datetime import datetime, timedelta, timezone

# Configuration
API_URL = "http://localhost:8000"
DEVICE_KEY = "test-secret-key"
DEVICE_ID = "550e8400-e29b-41d4-a716-446655440000"

# All species (must match database)
SPECIES = ["Lettuce", "Tomato", "Cucumber", "Basil"]

# Realistic ranges for each metric per plant type
# Base values with some variation per species
METRIC_PROFILES = {
    "Lettuce": {
        "air_temperature_c": {"base": 18, "amplitude": 4, "noise": 0.5},
        "rel_humidity_pct": {"base": 65, "amplitude": 10, "noise": 2},
        "soil_moisture_vwc_pct": {"base": 45, "amplitude": 8, "noise": 1},
        "light_ppfd_umol_m2_s": {"base": 200, "amplitude": 180, "noise": 15},
        "soil_ph": {"base": 6.2, "amplitude": 0.2, "noise": 0.05},
        "bioelectric_voltage_mv": {"base": 0, "amplitude": 1.5, "noise": 0.3},
    },
    "Tomato": {
        "air_temperature_c": {"base": 22, "amplitude": 5, "noise": 0.6},
        "rel_humidity_pct": {"base": 60, "amplitude": 12, "noise": 2.5},
        "soil_moisture_vwc_pct": {"base": 50, "amplitude": 10, "noise": 1.5},
        "light_ppfd_umol_m2_s": {"base": 350, "amplitude": 300, "noise": 20},
        "soil_ph": {"base": 6.5, "amplitude": 0.15, "noise": 0.03},
        "bioelectric_voltage_mv": {"base": 0, "amplitude": 2.0, "noise": 0.4},
    },
    "Cucumber": {
        "air_temperature_c": {"base": 24, "amplitude": 4, "noise": 0.5},
        "rel_humidity_pct": {"base": 70, "amplitude": 8, "noise": 2},
        "soil_moisture_vwc_pct": {"base": 55, "amplitude": 12, "noise": 2},
        "light_ppfd_umol_m2_s": {"base": 300, "amplitude": 250, "noise": 18},
        "soil_ph": {"base": 6.3, "amplitude": 0.2, "noise": 0.04},
        "bioelectric_voltage_mv": {"base": 0, "amplitude": 1.8, "noise": 0.35},
    },
    "Basil": {
        "air_temperature_c": {"base": 21, "amplitude": 3, "noise": 0.4},
        "rel_humidity_pct": {"base": 55, "amplitude": 10, "noise": 2},
        "soil_moisture_vwc_pct": {"base": 40, "amplitude": 8, "noise": 1},
        "light_ppfd_umol_m2_s": {"base": 250, "amplitude": 200, "noise": 12},
        "soil_ph": {"base": 6.0, "amplitude": 0.25, "noise": 0.04},
        "bioelectric_voltage_mv": {"base": 0, "amplitude": 1.2, "noise": 0.25},
    },
    "Radish": {
        "air_temperature_c": {"base": 16, "amplitude": 4, "noise": 0.5},
        "rel_humidity_pct": {"base": 60, "amplitude": 8, "noise": 1.5},
        "soil_moisture_vwc_pct": {"base": 48, "amplitude": 10, "noise": 1.5},
        "light_ppfd_umol_m2_s": {"base": 180, "amplitude": 150, "noise": 10},
        "soil_ph": {"base": 6.4, "amplitude": 0.2, "noise": 0.03},
        "bioelectric_voltage_mv": {"base": 0, "amplitude": 1.0, "noise": 0.2},
    },
}

def generate_value(t_hours: float, profile: dict, metric_key: str) -> float:
    """Generate a realistic value at time t (hours from start)."""
    base = profile["base"]
    amplitude = profile["amplitude"]
    noise = profile["noise"]
    
    # Different patterns based on metric type
    if metric_key == "light_ppfd_umol_m2_s":
        # Light follows sun pattern: peaks at noon, zero at night
        hour_of_day = (6 + t_hours) % 24  # Start at 6 AM
        if 6 <= hour_of_day <= 20:  # Daylight hours
            # Bell curve centered around noon (hour 12)
            normalized = (hour_of_day - 13) / 7  # -1 to 1 range
            light_factor = math.exp(-3 * normalized ** 2)
            value = base * light_factor + random.gauss(0, noise)
        else:
            value = random.gauss(0, noise / 5)  # Very low at night
        return max(0, value)
    
    elif metric_key == "air_temperature_c":
        # Temperature: warmer during day, cooler at night
        hour_of_day = (6 + t_hours) % 24
        day_offset = math.sin((hour_of_day - 6) * math.pi / 12) * amplitude
        value = base + day_offset + random.gauss(0, noise)
        return value
    
    elif metric_key == "rel_humidity_pct":
        # Humidity: inverse of temperature (higher at night)
        hour_of_day = (6 + t_hours) % 24
        day_offset = -math.sin((hour_of_day - 6) * math.pi / 12) * amplitude
        value = base + day_offset + random.gauss(0, noise)
        return max(30, min(95, value))
    
    elif metric_key == "soil_moisture_vwc_pct":
        # Soil moisture: gradual decrease with occasional watering
        base_decay = -t_hours * 0.3  # Slow decrease
        # Simulate watering events
        if int(t_hours) % 8 == 0:  # Water every 8 hours
            base_decay = 0
        value = base + base_decay + random.gauss(0, noise)
        return max(20, min(80, value))
    
    elif metric_key == "soil_ph":
        # pH: relatively stable with slight daily variation
        daily_var = math.sin(t_hours * math.pi / 12) * amplitude
        value = base + daily_var + random.gauss(0, noise)
        return max(5.0, min(8.0, value))
    
    elif metric_key == "bioelectric_voltage_mv":
        # Bioelectric: complex oscillating pattern (plant activity)
        # Multiple frequencies for organic look
        v1 = math.sin(t_hours * 2 * math.pi / 0.5) * amplitude * 0.5  # Fast oscillation
        v2 = math.sin(t_hours * 2 * math.pi / 2) * amplitude * 0.3    # Medium oscillation  
        v3 = math.sin(t_hours * 2 * math.pi / 6) * amplitude * 0.2    # Slow oscillation
        value = base + v1 + v2 + v3 + random.gauss(0, noise)
        return value
    
    return base + random.gauss(0, noise)


def ingest_samples(samples_payload: list, headers: dict) -> int:
    """Ingest samples in batches."""
    batch_size = 500
    total = 0
    
    for i in range(0, len(samples_payload), batch_size):
        batch = samples_payload[i:i+batch_size]
        payload = {"device_id": DEVICE_ID, "samples": batch}
        
        try:
            res = requests.post(f"{API_URL}/ingest", headers=headers, json=payload)
            if res.status_code == 201:
                total += res.json().get('processed', 0)
            else:
                print(f"  Error: {res.status_code}")
        except Exception as e:
            print(f"  Connection error: {e}")
    
    return total


def main():
    print("=" * 60)
    print("Sample Data Generator for All Plants & Metrics")
    print("=" * 60)
    
    headers = {
        "Authorization": f"Bearer {DEVICE_KEY}",
        "Content-Type": "application/json"
    }
    
    now = datetime.now(timezone.utc)
    hours = 24  # 24 hours of data
    samples_per_hour = 60  # 1 sample per minute
    
    total_samples = 0
    
    for species_name in SPECIES:
        print(f"\n{species_name}:")
        profile = METRIC_PROFILES[species_name]
        
        all_samples = []
        
        for metric_key, metric_profile in profile.items():
            for i in range(hours * samples_per_hour):
                t_hours = i / samples_per_hour
                ts = now - timedelta(hours=hours) + timedelta(hours=t_hours)
                value = generate_value(t_hours, metric_profile, metric_key)
                
                all_samples.append({
                    "species_name": species_name,
                    "metric_key": metric_key,
                    "value": round(value, 2),
                    "timestamp": ts.isoformat()
                })
        
        # Ingest all samples for this species
        count = ingest_samples(all_samples, headers)
        total_samples += count
        print(f"  Ingested {count} samples ({len(profile)} metrics × {hours * samples_per_hour} points)")
    
    print(f"\n{'=' * 60}")
    print(f"SUCCESS: Generated {total_samples:,} total samples")
    print("=" * 60)


if __name__ == "__main__":
    main()
