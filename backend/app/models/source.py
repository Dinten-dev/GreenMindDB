from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.orm import relationship
from app.database import Base


class Source(Base):
    """Academic and industry sources for growing condition data."""
    
    __tablename__ = "source"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(300), nullable=False)
    publisher = Column(String(200), nullable=False)  # e.g., "Cornell University Extension"
    year = Column(Integer, nullable=True)
    url = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)
    
    # Relationships
    target_ranges = relationship("TargetRange", back_populates="source")
    
    def __repr__(self):
        return f"<Source(id={self.id}, title='{self.title[:30]}...')>"
