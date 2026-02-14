from sqlalchemy import Column, Integer, Float, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class TargetRange(Base):
    """Target growing condition ranges linking species, metric, and source.
    
    One row per species + metric combination.
    Each plant has exactly 5 entries (one per metric).
    """
    
    __tablename__ = "target_range"
    
    id = Column(Integer, primary_key=True, index=True)
    species_id = Column(Integer, ForeignKey("species.id", ondelete="CASCADE"), nullable=False)
    metric_id = Column(Integer, ForeignKey("metric.id", ondelete="CASCADE"), nullable=False)
    
    # Required value ranges
    optimal_low = Column(Float, nullable=False)
    optimal_high = Column(Float, nullable=False)
    
    # Source reference (required)
    source_id = Column(Integer, ForeignKey("source.id", ondelete="SET NULL"), nullable=False)
    
    # Relationships
    species = relationship("Species", back_populates="target_ranges")
    metric = relationship("Metric", back_populates="target_ranges")
    source = relationship("Source", back_populates="target_ranges")
    
    def __repr__(self):
        return f"<TargetRange(species_id={self.species_id}, metric_id={self.metric_id})>"
