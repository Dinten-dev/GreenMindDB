"""
Scheduled Tasks for GreenMindDB

Uses APScheduler to run periodic jobs like the resampling pipeline.
"""
import logging
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.database import SessionLocal
from app.services.resampling_service import run_full_pipeline

logger = logging.getLogger(__name__)

# Create scheduler instance
scheduler = AsyncIOScheduler()


def run_resampling_job():
    """Execute the resampling pipeline for all species."""
    logger.info("Starting scheduled resampling job...")
    
    db = SessionLocal()
    try:
        results = run_full_pipeline(db)
        total = sum(v for v in results.values() if v > 0)
        logger.info(f"Resampling job complete. Processed {total} total rows across {len(results)} species.")
    except Exception as e:
        logger.error(f"Resampling job failed: {e}")
    finally:
        db.close()


def start_scheduler():
    """Start the APScheduler with configured jobs."""
    if not scheduler.running:
        # Run resampling pipeline every minute
        scheduler.add_job(
            run_resampling_job,
            trigger=IntervalTrigger(minutes=1),
            id="resampling_pipeline",
            name="1 Hz Resampling Pipeline",
            replace_existing=True
        )
        scheduler.start()
        logger.info("Scheduler started with resampling job (interval: 1 minute)")


def stop_scheduler():
    """Stop the APScheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


@asynccontextmanager
async def scheduler_lifespan(app):
    """Lifespan context manager for FastAPI to start/stop scheduler."""
    start_scheduler()
    yield
    stop_scheduler()
