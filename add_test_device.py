"""Quick script to add a test gateway for local development."""

from sqlalchemy import create_engine, text

import bcrypt

DATABASE_URL = "postgresql://plantuser:plantpass@localhost:5432/plantdb"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def create_test_gateway():
    engine = create_engine(DATABASE_URL)

    gateway_id = "550e8400-e29b-41d4-a716-446655440000"
    api_key = "test-secret-key"
    api_key_hash = hash_password(api_key)

    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM gateway WHERE id = :id"), {"id": gateway_id}
        )
        if result.fetchone():
            print(f"Gateway {gateway_id} already exists.")
            conn.execute(
                text("UPDATE gateway SET api_key_hash = :hash WHERE id = :id"),
                {"hash": api_key_hash, "id": gateway_id},
            )
            conn.commit()
            print("Updated API key for gateway.")
        else:
            conn.execute(
                text("""
                    INSERT INTO gateway (id, hardware_id, name, status, api_key_hash, is_active, greenhouse_id)
                    VALUES (:id, 'RPi-TEST-001', 'Test Gateway', 'offline', :hash, true,
                            (SELECT id FROM greenhouse LIMIT 1))
                """),
                {"id": gateway_id, "hash": api_key_hash},
            )
            conn.commit()
            print(f"Created gateway {gateway_id}")

    print(f"\nGateway ID: {gateway_id}")
    print(f"API Key: {api_key}")
    print("\nUse this in X-Api-Key header: " + api_key)


if __name__ == "__main__":
    try:
        create_test_gateway()
    except Exception as e:
        print(f"Error: {e}")
