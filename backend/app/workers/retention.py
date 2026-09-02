"""Standalone guarded retention worker."""

import logging
import time

from app.config import settings
from app.database import SessionLocal
from app.logging_config import setup_logging
from app.services.retention_service import run_retention
from app.workers.common import update_heartbeat

logger = logging.getLogger(__name__)


def main() -> None:
    setup_logging(settings.log_level)
    logger.info("Retention worker started")
    while True:
        db = SessionLocal()
        try:
            if not settings.retention_enabled:
                update_heartbeat(
                    db,
                    "retention",
                    "disabled",
                    {"dry_run": settings.retention_dry_run},
                )
            else:
                result = run_retention(db)
                update_heartbeat(db, "retention", "healthy", result)
        except Exception as exc:
            db.rollback()
            logger.exception("Retention worker cycle failed")
            try:
                update_heartbeat(
                    db,
                    "retention",
                    "failed",
                    {"error": f"{type(exc).__name__}: {exc}"[:500]},
                )
            except Exception:
                db.rollback()
        finally:
            db.close()
        time.sleep(settings.retention_interval_hours * 3600)


if __name__ == "__main__":
    main()
