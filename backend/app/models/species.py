from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.orm import relationship
from app.database import Base


class Species(Base):
    """Plant species in the database."""
    
    __tablename__ = "species"
    
    id = Column(Integer, primary_key=True, index=True)
    common_name = Column(String(100), nullable=False, unique=True, index=True)
    latin_name = Column(String(100), nullable=False)
    category = Column(String(50), nullable=False)  # e.g., "Leafy Green", "Fruit", "Herb"
    notes = Column(Text, nullable=True)
    
    # Relationships
    target_ranges = relationship("TargetRange", back_populates="species", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Species(id={self.id}, common_name='{self.common_name}')>"
