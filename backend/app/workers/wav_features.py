"""Standalone verified WAV feature worker."""

import logging
import time

from app.config import settings
from app.database import SessionLocal
from app.logging_config import setup_logging
from app.services.wav_feature_service import process_pending_wav_features
from app.workers.common import update_heartbeat

logger = logging.getLogger(__name__)


def main() -> None:
    setup_logging(settings.log_level)
    logger.info("WAV feature worker started")
    while True:
        db = SessionLocal()
        delay = settings.wav_feature_interval_minutes * 60
        try:
            if not settings.wav_feature_extraction_enabled:
                update_heartbeat(db, "wav_features", "disabled", {})
            else:
                result = process_pending_wav_features(db)
                update_heartbeat(db, "wav_features", "healthy", result)
                if result["verified"] or result["failed"]:
                    delay = settings.wav_feature_active_interval_seconds
        except Exception as exc:
            db.rollback()
            logger.exception("WAV feature worker cycle failed")
            try:
                update_heartbeat(
                    db,
                    "wav_features",
                    "failed",
                    {"error": f"{type(exc).__name__}: {exc}"[:500]},
                )
            except Exception:
                db.rollback()
        finally:
            db.close()
        time.sleep(delay)


if __name__ == "__main__":
    main()
