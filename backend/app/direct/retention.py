"""Direct-only retention with verified artifacts and explicit dry-run default."""

import time

from app.direct.database import transaction
from app.direct.models import Chunk, Piece, Revision, Segment


def run_retention(engine, settings, store, *, now=None):
    if not settings.retention_enabled:
        return {"disabled": True}
    now = time.time() if now is None else now
    report = {"dry_run": settings.retention_dry_run, "segments": 0, "chunks": 0}
    with transaction(engine) as db:
        segments = (
            db.query(Segment)
            .filter(
                Segment.sealed.is_(True),
                Segment.published_revision > 0,
                (Segment.bucket + 1) * 600 < now - settings.raw_days * 86400,
            )
            .order_by(Segment.bucket)
            .limit(100)
            .with_for_update(skip_locked=True)
            .all()
        )
        for segment in segments:
            revisions = db.query(Revision).filter_by(segment_id=segment.id).all()
            if not revisions or any(not revision.verified_at for revision in revisions):
                continue
            keys = {
                run["key"]
                for revision in revisions
                if revision.raw_deleted_at is None
                for run in revision.manifest["runs"]
            }
            if keys:
                report["segments"] += 1
                if not settings.retention_dry_run:
                    for key in keys:
                        store.delete(key)
                    for revision in revisions:
                        revision.raw_deleted_at = now
            if (
                not settings.retention_dry_run
                and (segment.bucket + 1) * 600 < now - settings.feature_days * 86400
            ):
                for revision in revisions:
                    if revision.raw_deleted_at is not None:
                        db.delete(revision)
        chunks = (
            db.query(Chunk)
            .filter(Chunk.payload.is_(None), Chunk.received_at < now - settings.raw_days * 86400)
            .order_by(Chunk.received_at)
            .limit(500)
            .with_for_update(skip_locked=True)
            .all()
        )
        report["chunks"] = len(chunks)
        if not settings.retention_dry_run:
            for chunk in chunks:
                db.query(Piece).filter_by(chunk_id=chunk.id).delete(synchronize_session=False)
                db.delete(chunk)
    return report
