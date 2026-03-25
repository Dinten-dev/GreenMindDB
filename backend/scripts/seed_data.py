"""Seed demo data: org, greenhouse, gateway, sensors, and 30 days of fake readings."""

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
from app.models.master import Gateway, Greenhouse, Sensor
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

        # ── Gateway (RPi) ────────────────────────
        gateway = Gateway(
            greenhouse_id=gh.id,
            hardware_id="RPi-DEMO-001",
            name="Demo Gateway Alpha",
            local_ip="192.168.1.100",
            fw_version="1.0.0",
            status="online",
            api_key_hash=get_password_hash("demo-api-key"),
            paired_at=datetime.now(UTC),
            last_seen=datetime.now(UTC),
        )
        db.add(gateway)
        db.flush()

        # ── Sensor (ESP32) ───────────────────────
        sensor = Sensor(
            gateway_id=gateway.id,
            mac_address="AA:BB:CC:DD:EE:01",
            name="Sensor Node Alpha",
            sensor_type="multi",
            status="online",
            last_seen=datetime.now(UTC),
        )
        db.add(sensor)
        db.flush()

        # ── Timeseries Data (30 days, 15-min intervals) ──
        sensor_kinds = [
            ("temperature", "°C"),
            ("humidity", "%"),
            ("soil_moisture", "%"),
            ("light_intensity", "lux"),
            ("bioelectric", "µV"),
        ]

        now = datetime.now(UTC)
        start = now - timedelta(days=30)
        interval = timedelta(minutes=15)
        current = start

        readings = []
        count = 0

        while current <= now:
            hour = current.hour
            day_of_year = current.timetuple().tm_yday

            for kind, unit in sensor_kinds:
                if kind == "temperature":
                    base = 23 + 5 * math.sin((hour - 6) * math.pi / 12)
                    value = base + random.gauss(0, 0.5)
                elif kind == "humidity":
                    base = 70 - 15 * math.sin((hour - 6) * math.pi / 12)
                    value = base + random.gauss(0, 2)
                elif kind == "soil_moisture":
                    base = 65 - (day_of_year % 3) * 5 + random.gauss(0, 1)
                    if hour == 7:
                        base = 75
                    value = max(30, min(90, base))
                elif kind == "light_intensity":
                    if 6 <= hour <= 20:
                        base = 800 * math.sin((hour - 6) * math.pi / 14)
                    else:
                        base = 5
                    value = max(0, base + random.gauss(0, 20))
                elif kind == "bioelectric":
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
                        kind=kind,
                        value=round(value, 2),
                        unit=unit,
                    )
                )
                count += 1

            if len(readings) >= 5000:
                db.bulk_save_objects(readings)
                readings = []

            current += interval

        if readings:
            db.bulk_save_objects(readings)

        db.commit()
        print(
            f"✅ Seed complete: 1 org, 1 user (demo@greenmind.io / Demo1234), "
            f"1 greenhouse, 1 gateway, 1 sensor, {len(sensor_kinds)} kinds, {count} readings"
        )

    except Exception as e:
        db.rollback()
        print(f"❌ Seed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
