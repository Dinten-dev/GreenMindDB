"""Explicit historical COPY catalog; the ordinary Direct catalog stays strict."""

from datetime import UTC, datetime

from .catalog import DirectCatalog
from .direct_history import complete_history
from .metadata_projection import one, scientific_row
from .policy import ArchiveBlocked, Recording, sensor_folder


class PublishedDirectCopyCatalog(DirectCatalog):
    def __init__(self, session_factory, bucket):
        super().__init__(session_factory, bucket, copy_only=True)
        self.current = DirectCatalog(session_factory, bucket)

    def historical_metadata(self, reference):
        from app.direct.models import Device, Enrollment, Revision, Segment

        with self.sessions() as db:
            segment = one(db, Segment, "direct", Segment.id == reference[0])
            if reference[1] == segment.published_revision:
                return None
            revision = one(
                db,
                Revision,
                "direct",
                Revision.segment_id == reference[0],
                Revision.revision == reference[1],
            )
            if (
                type(reference[1]) is not int
                or not 0 < reference[1] < segment.published_revision <= segment.revision
                or revision.raw_deleted_at is not None
                or not revision.verified_at
            ):
                raise ArchiveBlocked("Historical Direct revision is not published and verified")
            manifest = revision.manifest
            complete_history(manifest, segment.id, revision)
            if (
                manifest["device_id"],
                manifest["session_id"],
                manifest["revision"],
                manifest["bucket_start_us"],
            ) != (segment.device_id, segment.session_id, reference[1], segment.bucket * 600000000):
                raise ArchiveBlocked("Historical Direct manifest source identity mismatch")
            device = one(db, Device, "direct", Device.id == segment.device_id)
            enrollment = one(
                db, Enrollment, "direct", Enrollment.device_id == device.id, optional=True
            )
            result = {
                "observed_segment": scientific_row(segment, "direct", "direct_segment"),
                "revision": scientific_row(revision, "direct", "direct_revision"),
                "session": {
                    "device_id": manifest["device_id"],
                    "id": manifest["session_id"],
                    "config": manifest["config"],
                    "mode": manifest["source_mode"],
                },
                "capture_assignment": {
                    "status": "unknown",
                    "quarantined": True,
                    "reason": "source_has_no_immutable_capture_assignment_history",
                },
                "observed_assignment": {
                    "semantics": "observed_at_archive",
                    "device_id": device.id,
                    "organization_id": str(device.organization_id),
                    "zone_id": device.zone_id,
                    "legacy_sensor_id": device.legacy_sensor_id,
                    "mode": device.mode,
                    "hardware_id": enrollment.hardware_id if enrollment else None,
                },
            }

            from app.direct.capture_assignment import manifest_assignment, split
            from app.direct.models import Session

            capture = manifest_assignment(manifest)
            if capture is not None:
                session = one(
                    db,
                    Session,
                    "direct",
                    Session.device_id == segment.device_id,
                    Session.id == segment.session_id,
                )
                config, actual_capture = split(session.config)
                if (
                    actual_capture != capture
                    or config != manifest["config"]
                    or session.mode != capture["mode"]
                ):
                    raise ArchiveBlocked("Historical server capture assignment differs")
                result["session"] = scientific_row(session, "direct", "direct_session")
                result["capture_assignment"] = capture
            return result

    def inspect(self, segment_id, revision_number):
        metadata = self.historical_metadata([segment_id, revision_number])
        if metadata is None:
            return self.current.inspect(segment_id, revision_number)
        manifest = metadata["revision"]["manifest"]
        config = manifest["config"]
        # Historical hardware assignment is unknown. A stable source identity
        # avoids presenting today's enrollment as original acquisition metadata.
        sensor = sensor_folder(None, "direct", manifest["device_id"])
        return [
            Recording(
                "direct",
                f"{segment_id}:{revision_number}",
                self.bucket,
                run["key"],
                run["sha256"],
                44 + run["frame_count"] * config["channels"] * config["sample_bits"] // 8,
                datetime.fromtimestamp(
                    run["started_at_us"] / 1000000 + run["frame_count"] / config["sample_rate"], UTC
                ),
                datetime.fromtimestamp(metadata["revision"]["verified_at"], UTC),
                metadata["revision"]["manifest_sha256"],
                datetime.fromtimestamp(run["started_at_us"] / 1000000, UTC),
                sensor,
            )
            for run in manifest["runs"]
        ]
