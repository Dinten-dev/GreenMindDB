import sys
import os
import secrets
from datetime import datetime
import argparse

# Ensure we can import from backend
# sys.path.append(os.path.join(os.path.dirname(__file__), '../backend'))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from passlib.context import CryptContext

from app.macmini.config import get_settings
from app.models import Device, Greenhouse, Zone
from app.database import Base

# Setup hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def main():
    parser = argparse.ArgumentParser(description="Add a new device to GreenMindDB")
    parser.add_argument("--name", required=True, help="Device name")
    parser.add_argument("--serial", required=True, help="Device serial number")
    parser.add_argument("--type", required=True, help="Device type (gateway, esp32_station, etc.)")
    parser.add_argument("--greenhouse", help="Greenhouse name (optional, will use first available if not set)")
    
    args = parser.parse_args()
    
    settings = get_settings()
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        # Find or create greenhouse
        greenhouse = None
        if args.greenhouse:
            greenhouse = session.query(Greenhouse).filter(Greenhouse.name == args.greenhouse).first()
            if not greenhouse:
                print(f"Error: Greenhouse '{args.greenhouse}' not found.")
                return
        else:
            greenhouse = session.query(Greenhouse).first()
            if not greenhouse:
                # Create default greenhouse
                print("No greenhouse found. Creating default 'Main Greenhouse'.")
                greenhouse = Greenhouse(name="Main Greenhouse", location="Local")
                session.add(greenhouse)
                session.commit()
        
        # Check if device exists
        existing = session.query(Device).filter(Device.serial == args.serial).first()
        if existing:
            print(f"Device with serial '{args.serial}' already exists.")
            # We could rotate key here, but let's just exit for now specifically for "add"
            # But maybe the user wants a key? Let's rotate it if they asked to add it again?
            # For safety, let's just warn.
            print("To rotate key, implement a rotate script or use the dashboard.")
            return

        # Generate API Key
        # Format: gmd_<32_random_chars>
        api_key_plain = f"gmd_{secrets.token_urlsafe(24)}"
        api_key_hash = pwd_context.hash(api_key_plain)
        
        device = Device(
            name=args.name,
            serial=args.serial,
            type=args.type,
            greenhouse_id=greenhouse.id,
            status="online",
            api_key_hash=api_key_hash,
            api_key_last_rotated_at=datetime.utcnow(),
            is_active=True
        )
        
        session.add(device)
        session.commit()
        
        print(f"\n✅ Device Created Successfully!")
        print(f"--------------------------------")
        print(f"Name:       {device.name}")
        print(f"Serial:     {device.serial}")
        print(f"Type:       {device.type}")
        print(f"Greenhouse: {greenhouse.name}")
        print(f"--------------------------------")
        print(f"🔑 API KEY: {api_key_plain}")
        print(f"--------------------------------")
        print(f"⚠️  SAVE THIS KEY NOW. IT CANNOT BE SHOWN AGAIN.")
        
    except Exception as e:
        print(f"Error: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    main()
