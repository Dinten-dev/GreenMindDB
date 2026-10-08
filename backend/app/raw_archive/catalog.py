"""Bounded catalog adapters. Features remain in the existing PostgreSQL tables."""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import text

from .policy import ArchiveBlocked, Recording, cutoff, sensor_folder


def canonical(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class GatewayCatalog:
    def __init__(self, session_factory, max_file_bytes=64 * 1024**2, *, copy_only=False):
        self.sessions = session_factory
        self.max_file_bytes = max_file_bytes
        self.copy_only = copy_only

    def inspect(self, wav_id) -> Recording:
        from app.models.wav_file import WavFile

        with self.sessions() as db:
            wav = db.get(WavFile, wav_id)
            if wav is None or wav.raw_deleted_at is not None:
                raise ArchiveBlocked("Missing or previously deleted WAV")
            if self.copy_only:
                return Recording(
                    "gateway",
                    str(wav.id),
                    "greenmind-raw",
                    wav.s3_key,
                    wav.content_sha256,
                    wav.file_size_bytes,
                    wav.ended_at,
                    wav.created_at,
                    "",
                    wav.started_at,
                    sensor_folder(wav.sensor_mac, "gateway", wav.sensor_id),
                )
            feature = wav.feature
            from app.services.wav_feature_service import (
                EXTRACTOR_VERSION,
                PARAMETER_HASH,
                _persisted_feature_payload,
            )

            if (
                wav.feature_status != "verified"
                or not wav.feature_verified_at
                or feature is None
                or not feature.verified_at
                or feature.extractor_version != EXTRACTOR_VERSION
                or feature.parameter_hash != PARAMETER_HASH
                or feature.calibration_version != wav.calibration_version
                or feature.source_sha256 != wav.content_sha256
                or canonical(_persisted_feature_payload(feature)) != feature.feature_checksum
            ):
                raise ArchiveBlocked("Current persisted Gateway features are not verified")
            return Recording(
                "gateway",
                str(wav.id),
                "greenmind-raw",
                wav.s3_key,
                wav.content_sha256,
                wav.file_size_bytes,
                wav.ended_at,
                wav.created_at,
                feature.feature_checksum,
                wav.started_at,
                sensor_folder(wav.sensor_mac, "gateway", wav.sensor_id),
            )

    def prepare_features(self, wav_id, now):
        """Explicit one-file preparation, never invoked by API startup or the archive scan."""
        from app.models.wav_file import WavFile
        from app.services.wav_feature_service import extract_and_verify_wav_features

        with self.sessions() as db:
            wav = db.get(WavFile, wav_id)
            if (
                wav is None
                or wav.raw_deleted_at is not None
                or wav.ended_at >= cutoff(now)
                or wav.created_at >= cutoff(now)
                or not 0 < wav.file_size_bytes <= self.max_file_bytes
            ):
                raise ArchiveBlocked("Feature preparation is outside the approved bounds")
            extract_and_verify_wav_features(db, wav)

    def revalidate(self, recording):
        import uuid

        if self.inspect(uuid.UUID(recording.identity)) != recording:
            raise ArchiveBlocked("Gateway source/feature identity changed")

    def page(self, cursor, before, limit):
        """Indexed keyset sweep, with next-sweep reconciliation for late/backdated rows."""
        import uuid

        from app.models.wav_file import WavFile

        with self.sessions() as db:
            query = db.query(WavFile.id).filter(
                WavFile.raw_deleted_at.is_(None),
                WavFile.created_at < before,
                WavFile.ended_at < before,
            )
            if cursor:
                query = query.filter(WavFile.id > uuid.UUID(cursor[0]))
            return [[str(row[0])] for row in query.order_by(WavFile.id).limit(limit)]

    def resolve(self, reference):
        import uuid

        return [self.inspect(uuid.UUID(reference[0]))]

    def fingerprints(self, references):
        import uuid

        from app.models.wav_file import WavFile as W

        if not references:
            return {}
        if len(references) > 100:
            raise ArchiveBlocked("Fingerprint page exceeds bound")
        with self.sessions() as db:
            rows = (
                db.query(
                    W.id,
                    W.s3_key,
                    W.content_sha256,
                    W.file_size_bytes,
                    W.started_at,
                    W.ended_at,
                    W.created_at,
                    W.sensor_mac,
                    W.sensor_id,
                    W.raw_deleted_at,
                )
                .filter(W.id.in_([uuid.UUID(ref[0]) for ref in references]))
                .all()
            )
            return {
                json.dumps([str(row[0])]): canonical({"values": [str(value) for value in row]})
                for row in rows
            }

    def recent_page(self, cursor, after, before, limit):
        """Recent arrivals first; prepared (created_at,id) index bounds this read."""
        import uuid

        from sqlalchemy import tuple_

        from app.models.wav_file import WavFile

        with self.sessions() as db:
            query = db.query(WavFile.created_at, WavFile.id).filter(
                WavFile.created_at >= after,
                WavFile.created_at < before,
                WavFile.ended_at < before,
                WavFile.raw_deleted_at.is_(None),
            )
            if cursor:
                query = query.filter(
                    tuple_(WavFile.created_at, WavFile.id)
                    > (datetime.fromisoformat(cursor[0]), uuid.UUID(cursor[1]))
                )
            rows = query.order_by(WavFile.created_at, WavFile.id).limit(limit).all()
            return [([str(row.id)], [row.created_at.isoformat(), str(row.id)]) for row in rows]


class DirectCatalog:
    def __init__(self, session_factory, bucket, *, copy_only=False):
        if not bucket.startswith("greenmind-direct-"):
            raise ArchiveBlocked("Direct needs its own source bucket")
        self.sessions, self.bucket = session_factory, bucket
        self.copy_only = copy_only

    def inspect(self, segment_id, revision_number):
        from app.direct.models import Enrollment, Revision, Segment

        with self.sessions() as db:
            segment = db.get(Segment, segment_id)
            revision = db.get(Revision, (segment_id, revision_number))
            if (
                segment is None
                or not segment.sealed
                or revision is None
                or revision.raw_deleted_at is not None
                or not revision.verified_at
                or segment.published_revision != segment.revision
                or revision_number != segment.published_revision
            ):
                raise ArchiveBlocked("Direct segment is not sealed and verified")
            manifest = revision.manifest
            if canonical(manifest) != revision.manifest_sha256:
                raise ArchiveBlocked("Direct feature manifest checksum mismatch")
            if (manifest["device_id"], manifest["session_id"], manifest["revision"]) != (
                segment.device_id,
                segment.session_id,
                revision_number,
            ):
                raise ArchiveBlocked("Direct manifest identity mismatch")
            # Do not remove RAW before the dashboard has its durable projection.
            if not self.copy_only:
                projection = db.execute(
                    text("""SELECT source_revision, error FROM
                    direct_visual_segment WHERE segment_id=:id"""),
                    {"id": segment_id},
                ).first()
                points = db.execute(
                    text("""SELECT 1 FROM direct_visual_point
                    WHERE segment_id=:id LIMIT 1"""),
                    {"id": segment_id},
                ).first()
                if (
                    not projection
                    or projection[0] != segment.revision
                    or projection[1] is not None
                    or not points
                ):
                    raise ArchiveBlocked("Direct dashboard projection missing or stale")
            config = manifest["config"]
            enrollment = (
                db.query(Enrollment.hardware_id)
                .filter(Enrollment.device_id == segment.device_id)
                .first()
            )
            sensor = sensor_folder(
                enrollment[0] if enrollment else None, "direct", segment.device_id
            )
            records = []
            for run in manifest["runs"]:
                if not run.get("features"):
                    raise ArchiveBlocked("Direct channel features missing")
                # Generated assembler WAVs have the standard 44-byte PCM header.
                size = 44 + run["frame_count"] * config["channels"] * config["sample_bits"] // 8
                ended = (
                    run["started_at_us"] / 1_000_000 + run["frame_count"] / config["sample_rate"]
                )
                records.append(
                    Recording(
                        "direct",
                        f"{segment_id}:{revision_number}",
                        self.bucket,
                        run["key"],
                        run["sha256"],
                        size,
                        datetime.fromtimestamp(max(ended, (segment.bucket + 1) * 600), UTC),
                        datetime.fromtimestamp(max(segment.updated_at, revision.verified_at), UTC),
                        revision.manifest_sha256,
                        datetime.fromtimestamp(run["started_at_us"] / 1_000_000, UTC),
                        sensor,
                    )
                )
            return records

    def revalidate(self, recording):
        segment_id, revision = recording.identity.split(":")
        if recording not in self.inspect(segment_id, int(revision)):
            raise ArchiveBlocked("Direct source/features changed")

    def page(self, cursor, before, limit):
        from sqlalchemy import and_, or_

        from app.direct.models import Revision, Segment

        with self.sessions() as db:
            query = (
                db.query(Revision.segment_id, Revision.revision)
                .join(
                    Segment,
                    Segment.id == Revision.segment_id,
                )
                .filter(
                    Segment.sealed.is_(True),
                    Revision.raw_deleted_at.is_(None),
                    Segment.updated_at < before.timestamp(),
                    Revision.verified_at < before.timestamp(),
                )
            )
            if cursor:
                query = query.filter(
                    or_(
                        Revision.segment_id > cursor[0],
                        and_(Revision.segment_id == cursor[0], Revision.revision > cursor[1]),
                    )
                )
            return [
                list(row)
                for row in query.order_by(Revision.segment_id, Revision.revision).limit(limit)
            ]

    def resolve(self, reference):
        # Return EVERY run of a revision. Per-file budgets are applied by the runner.
        return self.inspect(reference[0], reference[1])

    def fingerprints(self, references):
        from sqlalchemy import tuple_

        from app.direct.models import Revision as R
        from app.direct.models import Segment as S

        if not references:
            return {}
        if len(references) > 100:
            raise ArchiveBlocked("Fingerprint page exceeds bound")
        with self.sessions() as db:
            rows = (
                db.query(
                    R.segment_id,
                    R.revision,
                    R.manifest_sha256,
                    R.verified_at,
                    R.raw_deleted_at,
                    S.updated_at,
                    S.sealed,
                    S.revision,
                    S.published_revision,
                )
                .join(S, S.id == R.segment_id)
                .filter(tuple_(R.segment_id, R.revision).in_([tuple(ref) for ref in references]))
                .all()
            )
            return {
                json.dumps([row[0], row[1]]): canonical({"values": [str(value) for value in row]})
                for row in rows
            }

    def recent_page(self, cursor, after, before, limit):
        from sqlalchemy import tuple_

        from app.direct.models import Revision, Segment

        with self.sessions() as db:
            query = (
                db.query(Revision.verified_at, Revision.segment_id, Revision.revision)
                .join(Segment, Segment.id == Revision.segment_id)
                .filter(
                    Revision.verified_at >= after.timestamp(),
                    Revision.verified_at < before.timestamp(),
                    Revision.raw_deleted_at.is_(None),
                    Segment.sealed.is_(True),
                    Segment.updated_at < before.timestamp(),
                )
            )
            if cursor:
                query = query.filter(
                    tuple_(Revision.verified_at, Revision.segment_id, Revision.revision)
                    > tuple(cursor)
                )
            rows = (
                query.order_by(Revision.verified_at, Revision.segment_id, Revision.revision)
                .limit(limit)
                .all()
            )
            return [([row.segment_id, row.revision], list(row)) for row in rows]
