from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.orm import relationship
from app.database import Base


class Metric(Base):
    """Measurable growing conditions (temperature, humidity, pH, etc.)."""
    
    __tablename__ = "metric"
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(50), nullable=False, unique=True, index=True)  # e.g., "air_temperature_c"
    label = Column(String(100), nullable=False)  # e.g., "Air Temperature"
    unit = Column(String(30), nullable=False)  # e.g., "°C"
    description = Column(Text, nullable=True)
    
    # Relationships
    target_ranges = relationship("TargetRange", back_populates="metric")
    
    def __repr__(self):
        return f"<Metric(id={self.id}, key='{self.key}')>"
