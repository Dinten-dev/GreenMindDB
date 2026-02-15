"""Shared SQLAlchemy Base – imported by all models and by Alembic."""
from sqlalchemy.orm import declarative_base

Base = declarative_base()
