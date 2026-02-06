from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models import Metric
from app.schemas import MetricResponse

router = APIRouter(prefix="/metrics", tags=["Metrics"])


@router.get("", response_model=List[MetricResponse])
def list_metrics(db: Session = Depends(get_db)):
    """List all available metrics."""
    metrics = db.query(Metric).order_by(Metric.key).all()
    return metrics
