"""Independent worker process. A failed Direct worker cannot stop Legacy."""

import logging
import time

from app.direct.assembler import assemble_one, release_verified_chunks
from app.direct.config import DirectSettings
from app.direct.database import make_engine, transaction
from app.direct.models import Heartbeat
from app.direct.payloads import OffloadCursor, offload_verified_chunks
from app.direct.retention import run_retention
from app.direct.storage import ArtifactStore

logger = logging.getLogger(__name__)


def release_payloads(engine, cfg, store, *, cursor=None):
    if cfg.payload_offload_enabled:
        return offload_verified_chunks(
            engine,
            store,
            store.get_archive,
            archive_available=store.archive_available,
            cursor=cursor,
        )
    if cfg.payload_release_requires_archive:
        # A rollback may pause offload without reverting to single-copy cleanup.
        return 0
    return release_verified_chunks(engine)


def main():
    logging.basicConfig(level=logging.INFO)
    cfg = DirectSettings()
    if not cfg.ingest_enabled:
        logger.info("direct_worker_disabled")
        return
    engine, store = make_engine(cfg), ArtifactStore(cfg)
    next_retention = 0
    offload_cursor = OffloadCursor()
    while True:
        status = "healthy"
        processed = False
        try:
            processed = assemble_one(engine, cfg, store)
            released = release_payloads(engine, cfg, store, cursor=offload_cursor)
            if cfg.payload_offload_enabled and released:
                logger.info("direct_verified_payload_offload bytes=%s", released)
            if time.time() >= next_retention:
                run_retention(engine, cfg, store)
                next_retention = time.time() + 3600
        except Exception as exc:
            status = "degraded"
            logger.error("direct_worker_failed type=%s", type(exc).__name__)
        try:
            with transaction(engine) as db:
                db.merge(Heartbeat(name="assembler", updated_at=time.time(), status=status))
        except Exception as exc:
            logger.error("direct_heartbeat_failed type=%s", type(exc).__name__)
        if not processed:
            time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    main()
