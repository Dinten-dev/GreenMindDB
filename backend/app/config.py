import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    database_url: str = "postgresql://plantuser:plantpass@localhost:5432/plantdb"
    cors_origins: str = "http://localhost:3000"
    
    class Config:
        env_file = ".env"


settings = Settings()
