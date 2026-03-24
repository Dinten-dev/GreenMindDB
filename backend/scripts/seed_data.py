"""Seed demo data: org, greenhouse, device, sensors, and 30 days of fake readings."""

import math
import os
import random
import sys
from datetime import UTC, datetime, timedelta

# Add parent dir to path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import get_password_hash
from app.models.master import Device, Greenhouse, Sensor
from app.models.timeseries import SensorReading
from app.models.user import Organization, Role, User

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://plantuser:plantpass@localhost:5432/plantdb",
)

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)


def seed():
    db = Session()
    try:
        # ── Organization ──────────────────────────
        org = Organization(name="GreenMind Demo")
        db.add(org)
        db.flush()

        # ── User ─────────────────────────────────
        user = User(
            email="demo@greenmind.io",
            name="Demo User",
            password_hash=get_password_hash("Demo1234"),
            role=Role.OWNER,
            organization_id=org.id,
            is_verified=True,
        )
        db.add(user)
        db.flush()

        # ── Greenhouse ───────────────────────────
        gh = Greenhouse(
            organization_id=org.id,
            name="Main Greenhouse",
            location="Basel, Switzerland",
        )
        db.add(gh)
        db.flush()

        # ── Device ───────────────────────────────
        device = Device(
            greenhouse_id=gh.id,
            serial="ESP32-DEMO-001",
            name="Sensor Node Alpha",
            type="esp32",
            fw_version="2.1.0",
            status="online",
            api_key_hash=get_password_hash("demo-api-key"),
            paired_at=datetime.now(UTC),
            last_seen=datetime.now(UTC),
        )
        db.add(device)
        db.flush()

        # ── Sensors ──────────────────────────────
        sensor_configs = [
            ("temperature", "°C", "Temperature"),
            ("humidity", "%", "Humidity"),
            ("soil_moisture", "%", "Soil Moisture"),
            ("light_intensity", "lux", "Light Intensity"),
            ("bioelectric", "µV", "Bioelectric Signal"),
        ]
        sensors = []
        for kind, unit, label in sensor_configs:
            s = Sensor(
                device_id=device.id,
                kind=kind,
                unit=unit,
                label=f"{device.name} – {label}",
            )
            db.add(s)
            db.flush()
            sensors.append((s, kind))

        # ── Timeseries Data (30 days, 15-min intervals) ──
        now = datetime.now(UTC)
        start = now - timedelta(days=30)
        interval = timedelta(minutes=15)
        current = start

        readings = []
        count = 0

        while current <= now:
            hour = current.hour
            day_of_year = current.timetuple().tm_yday

            for sensor, kind in sensors:
                if kind == "temperature":
                    # Diurnal cycle: 18-28°C
                    base = 23 + 5 * math.sin((hour - 6) * math.pi / 12)
                    value = base + random.gauss(0, 0.5)
                elif kind == "humidity":
                    # Inverse of temperature: 55-85%
                    base = 70 - 15 * math.sin((hour - 6) * math.pi / 12)
                    value = base + random.gauss(0, 2)
                elif kind == "soil_moisture":
                    # Slowly declining, jumps on "watering"
                    base = 65 - (day_of_year % 3) * 5 + random.gauss(0, 1)
                    if hour == 7:
                        base = 75  # Watering time
                    value = max(30, min(90, base))
                elif kind == "light_intensity":
                    # Bell curve during day
                    if 6 <= hour <= 20:
                        base = 800 * math.sin((hour - 6) * math.pi / 14)
                    else:
                        base = 5
                    value = max(0, base + random.gauss(0, 20))
                elif kind == "bioelectric":
                    # Noisy signal correlated with light
                    if 6 <= hour <= 20:
                        base = 150 + 100 * math.sin((hour - 6) * math.pi / 14)
                    else:
                        base = 50
                    value = max(0, base + random.gauss(0, 15))
                else:
                    value = random.uniform(0, 100)

                readings.append(
                    SensorReading(
                        timestamp=current,
                        sensor_id=sensor.id,
                        value=round(value, 2),
                        unit=sensor.unit,
                    )
                )
                count += 1

            # Batch insert every 5000 readings
            if len(readings) >= 5000:
                db.bulk_save_objects(readings)
                readings = []

            current += interval

        # Insert remaining
        if readings:
            db.bulk_save_objects(readings)

        db.commit()
        print(
            f"✅ Seed complete: 1 org, 1 user (demo@greenmind.io / Demo1234), 1 greenhouse, 1 device, {len(sensor_configs)} sensors, {count} readings"
        )

    except Exception as e:
        db.rollback()
        print(f"❌ Seed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
