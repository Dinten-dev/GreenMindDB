import asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.master import Sensor
from app.models.timeseries import SensorReading
from datetime import datetime, UTC

def main():
    engine = create_engine("postgresql://gm_admin:gm_secure_pwd_99@127.0.0.1:5433/greenmind")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    
    sensors = db.query(Sensor).all()
    print(f"Total sensors: {len(sensors)}")
    
    now = datetime.now(UTC)
    
    for s in sensors:
        latest = db.query(SensorReading).filter(SensorReading.sensor_id == s.id, SensorReading.kind == 'bio_signal').order_by(SensorReading.timestamp.desc()).first()
        if latest:
            diff = (now - latest.timestamp.replace(tzinfo=UTC)).total_seconds()
            print(f"Sensor {s.id} ({s.mac_address}): Last reading {diff} seconds ago. Value: {latest.value} {latest.unit}")
        else:
            print(f"Sensor {s.id} ({s.mac_address}): NO DATA")

if __name__ == "__main__":
    main()
