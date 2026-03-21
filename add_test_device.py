from sqlalchemy import create_engine, text
import bcrypt
import uuid

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

# Database URL (from docker-compose)
DATABASE_URL = "postgresql://plantuser:plantpass@localhost:5432/plantdb"

def create_test_device():
    engine = create_engine(DATABASE_URL)

    device_id = "550e8400-e29b-41d4-a716-446655440000"
    api_key = "test-secret-key"
    api_key_hash = hash_password(api_key)

    with engine.connect() as conn:
        # Check if exists
        result = conn.execute(text("SELECT id FROM device WHERE id = :id"), {"id": device_id})
        if result.fetchone():
            print(f"Device {device_id} already exists.")
            # Update key just in case
            conn.execute(
                text("UPDATE device SET api_key_hash = :hash WHERE id = :id"),
                {"hash": api_key_hash, "id": device_id}
            )
            conn.commit()
            print(f"Updated API key for device.")
        else:
            conn.execute(
                text("""
                    INSERT INTO device (id, name, device_type, api_key_hash, is_active)
                    VALUES (:id, 'Test Device', 'sensor', :hash, true)
                """),
                {"id": device_id, "hash": api_key_hash}
            )
            conn.commit()
            print(f"Created device {device_id}")

    print(f"\nDevice ID: {device_id}")
    print(f"API Key: {api_key}")
    print("\nUse this token in Authorization header: Bearer " + api_key)

if __name__ == "__main__":
    try:
        create_test_device()
    except Exception as e:
        print(f"Error: {e}")
