import sys
import os
import json

# Add backend directory to path so we can import app
sys.path.append("/app")

from app.database import SessionLocal
from app.models.user import User, Role
from app.auth import create_access_token
from datetime import timedelta

db = SessionLocal()
user = db.query(User).filter(User.role == Role.OWNER).first()
if not user:
    user = db.query(User).filter(User.role == Role.ADMIN).first()
if not user:
    print("No admin user found")
    sys.exit(1)

data = {"sub": str(user.id), "role": user.role.value if hasattr(user.role, "value") else str(user.role)}
token = create_access_token(data=data, expires_delta=timedelta(minutes=60))
print(f"TOKEN_START_HERE{token}TOKEN_END_HERE")
