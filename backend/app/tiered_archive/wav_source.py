"""Gateway and Direct WAV bundles retain scientific metadata and old URLs."""

import os
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.raw_archive.metadata_projection import one, rows, scientific_row
from app.raw_archive.policy import ArchiveBlocked, Recording, checksum
from app.raw_archive.policy import Ledger as RawLedger

from .codec import digest, epoch_us
from .policy import Candidate
from .storage import MAX_BYTES, disk_reserve


def record_json(record):
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in asdict(record).items()
    }


class WavSource:
    def __init__(
        self,
        sessions,
        catalog,
        objects,
        kind,
        environment,
        raw_config,
        checkpoint,
        *,
        copy_published_history=False,
    ):
        if type(copy_published_history) is not bool:
            raise ArchiveBlocked("Explicit boolean COPY-only history scope required")
        self.sessions, self.catalog, self.objects = sessions, catalog, objects
        self.kind, self.environment, self.raw_config, self.checkpoint = (
            kind,
            environment,
            raw_config,
            checkpoint,
        )
        self.expected, self.db = None, None
        self.deletion_guard = None
        self.deletion_intent_active = False
        self.copy_published_history = copy_published_history

    def metadata(self, reference, *, maximum_bytes=None, maximum_wavs=None):
        records = self.catalog.resolve(reference)
        if not records or len(records) > 1024:
            raise ArchiveBlocked("No verified WAVs in revision")
        if maximum_wavs is not None and (maximum_wavs < 1 or len(records) > maximum_wavs):
            raise ArchiveBlocked("WAV revision exceeds bounded file count")
        total = sum(record.size for record in records)
        budget = int(os.environ.get("TIERED_ARCHIVE_MAX_WAV_BUNDLE_BYTES", MAX_BYTES))
        if maximum_bytes is not None:
            budget = min(budget, maximum_bytes)
        if (
            not 0 < budget <= MAX_BYTES
            or total > budget
            or any(not 0 < record.size <= budget for record in records)
        ):
            raise ArchiveBlocked("WAV revision exceeds bounded bundle budget")
        if self.kind == "direct" and self.copy_published_history:
            historical = self.catalog.historical_metadata(reference)
            if historical is not None:
                from app.raw_archive.direct_history import COPY_SCOPE

                manifest = historical["revision"]["manifest"]
                if any(
                    record.feature_digest != historical["revision"]["manifest_sha256"]
                    for record in records
                ):
                    raise ArchiveBlocked("Historical source changed between metadata reads")
                config = manifest["config"]
                from app.direct.capture_assignment import manifest_assignment
                from app.raw_archive.wav_context import DIRECT_CAPTURE_SCOPE_V3, captured_direct

                capture = manifest_assignment(manifest)
                organization = historical["observed_assignment"]["organization_id"]
                metadata = historical
                scope = COPY_SCOPE
                if capture is not None:
                    metadata = captured_direct(
                        {
                            "segment": historical["observed_segment"],
                            "revision": historical["revision"],
                            "session": historical["session"],
                            "assignment": {
                                k: v
                                for k, v in historical["observed_assignment"].items()
                                if k != "semantics"
                            },
                            "direct_visual_segment": None,
                        }
                    )
                    organization, scope = capture["organization_id"], DIRECT_CAPTURE_SCOPE_V3
                return (
                    {
                        "identity": digest(reference),
                        # Preserve existing current-organization access routing;
                        # the body explicitly distinguishes it from capture truth.
                        "organization": organization,
                        "sensor": manifest["device_id"],
                        "time_us": epoch_us(min(record.started_at for record in records)),
                        "body": {
                            "reference": reference,
                            "copy_scope": scope,
                            "metadata": metadata,
                            "originals": [{"recording": record_json(r)} for r in records],
                        },
                    },
                    {
                        run["key"]: {
                            "frames": run["frame_count"],
                            "rate": config["sample_rate"],
                            "channels": config["channels"],
                            "width": config["sample_bits"] // 8,
                        }
                        for run in manifest["runs"]
                    },
                    dict(
                        complete=False,
                        features_verified=True,
                        projections_verified=False,
                        dependencies_complete=False,
                        quarantined=capture is None,
                    ),
                )
        with self.sessions() as db:
            if self.kind == "gateway":
                from app.models.wav_file import WavFeature, WavFeatureVersion, WavFile

                wav = one(db, WavFile, "gateway", WavFile.id == uuid.UUID(records[0].identity))
                feature = one(db, WavFeature, "gateway", WavFeature.wav_file_id == wav.id)
                versions = rows(
                    db, WavFeatureVersion, "gateway", WavFeatureVersion.wav_file_id == wav.id
                )
                metadata = {
                    "wav": scientific_row(wav, "gateway", "wav_file"),
                    "feature": scientific_row(feature, "gateway", "wav_feature"),
                    "feature_versions": [
                        scientific_row(v, "gateway", "wav_feature_version") for v in versions
                    ],
                }
                row = db.execute(
                    text("""SELECT z.organization_id FROM sensor s JOIN gateway g ON g.id=s.gateway_id
                    JOIN zone z ON z.id=g.zone_id WHERE s.id=:id"""),
                    {"id": wav.sensor_id},
                ).one()
                organization, sensor = str(row[0]), str(wav.sensor_id)
                assignment = db.execute(
                    text("""SELECT s.gateway_id,g.zone_id FROM sensor s
                    JOIN gateway g ON g.id=s.gateway_id WHERE s.id=:id"""),
                    {"id": wav.sensor_id},
                ).one()
                metadata["assignment"] = {
                    "sensor_id": sensor,
                    "gateway_id": str(assignment[0]),
                    "zone_id": str(assignment[1]),
                    "organization_id": organization,
                }
                complete = wav.timing_status == "complete"
                headers = {
                    wav.s3_key: {
                        "frames": feature.sample_count,
                        "rate": feature.sample_rate,
                        "channels": 1,
                        "width": 2,
                    }
                }
                projections = db.execute(
                    text("SELECT status='verified' FROM visual_wav WHERE wav_id=:id"),
                    {"id": wav.id},
                ).scalar()
                if self.copy_published_history:
                    from app.raw_archive.wav_catalog import COLUMNS

                    visual = (
                        db.execute(
                            text(
                                "SELECT "
                                + ",".join(COLUMNS["gateway"]["visual_wav"])
                                + " FROM visual_wav WHERE wav_id=:id"
                            ),
                            {"id": wav.id},
                        )
                        .mappings()
                        .one_or_none()
                    )
                    metadata["visual_wav"] = dict(visual) if visual is not None else None
            else:
                from app.direct.models import Device, Enrollment, Revision, Segment
                from app.direct.models import Session as CaptureSession

                segment = one(db, Segment, "direct", Segment.id == reference[0])
                revision = one(
                    db,
                    Revision,
                    "direct",
                    Revision.segment_id == reference[0],
                    Revision.revision == reference[1],
                )
                device = one(db, Device, "direct", Device.id == segment.device_id)
                session = one(
                    db,
                    CaptureSession,
                    "direct",
                    CaptureSession.device_id == segment.device_id,
                    CaptureSession.id == segment.session_id,
                )
                metadata = {
                    "segment": scientific_row(segment, "direct", "direct_segment"),
                    "revision": scientific_row(revision, "direct", "direct_revision"),
                    "session": scientific_row(session, "direct", "direct_session"),
                }
                from app.direct.capture_assignment import manifest_assignment, split

                if split(session.config)[1] != manifest_assignment(revision.manifest):
                    raise ArchiveBlocked("Verified server capture Session/Revision mismatch")
                organization, sensor = str(device.organization_id), segment.device_id
                enrollment = one(
                    db, Enrollment, "direct", Enrollment.device_id == device.id, optional=True
                )
                metadata["assignment"] = {
                    "device_id": device.id,
                    "organization_id": organization,
                    "zone_id": device.zone_id,
                    "legacy_sensor_id": device.legacy_sensor_id,
                    "mode": device.mode,
                    "hardware_id": enrollment.hardware_id if enrollment else None,
                }
                if self.copy_published_history:
                    from app.raw_archive.wav_context import DIRECT_VISUAL_FIELDS

                    visual = (
                        db.execute(
                            text(
                                "SELECT "
                                + ",".join(sorted(DIRECT_VISUAL_FIELDS))
                                + " FROM direct_visual_segment WHERE segment_id=:id AND (error IS NULL OR octet_length(error)<=4096)"
                            ),
                            {"id": segment.id},
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if (
                        visual is None
                        and db.execute(
                            text("SELECT 1 FROM direct_visual_segment WHERE segment_id=:id"),
                            {"id": segment.id},
                        ).first()
                        is not None
                    ):
                        raise ArchiveBlocked(
                            "Observed Direct visual parent exceeds bounded metadata scope"
                        )
                    metadata["direct_visual_segment"] = dict(visual) if visual is not None else None
                complete = (
                    segment.sealed
                    and segment.revision == segment.published_revision
                    and revision.manifest.get("status") == "complete"
                )
                projections = True  # DirectCatalog.inspect verifies revision-bound durable points.
                config = revision.manifest["config"]
                headers = {
                    run["key"]: {
                        "frames": run["frame_count"],
                        "rate": config["sample_rate"],
                        "channels": config["channels"],
                        "width": config["sample_bits"] // 8,
                    }
                    for run in revision.manifest["runs"]
                }
        at = min(record.started_at for record in records)
        envelope = {
            "identity": digest(reference),
            "organization": organization,
            "sensor": sensor,
            "time_us": epoch_us(at),
            "body": {
                "reference": reference,
                "metadata": metadata,
                "originals": [{"recording": record_json(record)} for record in records],
            },
        }
        if self.copy_published_history:
            from app.raw_archive.wav_context import (
                DIRECT_CAPTURE_SCOPE_V3,
                DIRECT_COPY_SCOPE_V2,
                GATEWAY_COPY_SCOPE_V2,
                captured_direct,
                copy_flags,
                observe,
            )

            if self.kind == "direct":
                from app.direct.capture_assignment import split

                _, capture = split(metadata["session"]["config"])
                if capture is not None:
                    envelope["body"]["copy_scope"] = DIRECT_CAPTURE_SCOPE_V3
                    envelope["body"]["metadata"] = captured_direct(metadata)
                    envelope["organization"] = capture["organization_id"]
                    return envelope, headers, dict(copy_flags(), quarantined=False)

            # These live joins prove present access routing, not assignment at
            # acquisition time. This branch is used by both real COPY producers;
            # strict deletion sources and old archive formats remain unchanged.
            copy_scope = GATEWAY_COPY_SCOPE_V2 if self.kind == "gateway" else DIRECT_COPY_SCOPE_V2
            envelope["body"]["copy_scope"] = copy_scope
            envelope["body"]["metadata"] = observe(metadata, self.kind, copy_scope=copy_scope)
            return envelope, headers, copy_flags()
        return (
            envelope,
            headers,
            dict(
                complete=bool(complete),
                features_verified=True,
                projections_verified=bool(projections),
                dependencies_complete=True,
            ),
        )

    def prepare(self, reference, *, maximum_bytes=None, maximum_wavs=None):
        envelope, headers, flags = self.metadata(
            reference, maximum_bytes=maximum_bytes, maximum_wavs=maximum_wavs
        )
        for item in envelope["body"]["originals"]:
            self.checkpoint()
            item["snapshot"] = self.objects.snapshot(Recording.from_receipt(item["recording"]))
        return envelope, headers, flags

    def export(self, reference, folder, *, maximum_bytes=None, maximum_wavs=None):
        envelope, headers, flags = self.prepare(
            reference, maximum_bytes=maximum_bytes, maximum_wavs=maximum_wavs
        )
        originals = envelope["body"]["originals"]
        disk_reserve(folder, sum(item["recording"]["size"] for item in originals) * 2)
        artifacts = []
        for number, item in enumerate(originals):
            self.checkpoint()
            record = Recording.from_receipt(item["recording"])
            path = folder / f"original-{number}.wav"
            self.objects.download(record, item["snapshot"], path)
            if checksum(path) != record.sha256 or path.stat().st_size != record.size:
                raise ArchiveBlocked("Source WAV differs from scientific metadata")
            artifacts.append(
                (
                    path,
                    {
                        "format": "wav",
                        "sha256": record.sha256,
                        "bytes": record.size,
                        "key": record.remote_key(self.environment),
                        "wav_header": headers[record.key],
                    },
                )
            )
        if envelope["body"].get("copy_scope"):
            # Single-call publishing has the same metadata/object change fence
            # as the resumable wav_copy.advance path.
            fresh, _, fresh_flags = self.metadata(reference)
            scientific = dict(
                envelope,
                body=dict(
                    envelope["body"],
                    originals=[{"recording": item["recording"]} for item in originals],
                ),
            )
            if digest(scientific) != digest(fresh) or fresh_flags != flags:
                raise ArchiveBlocked("Historical scientific source changed during WAV copy")
            for item in originals:
                if (
                    self.objects.snapshot(Recording.from_receipt(item["recording"]))
                    != item["snapshot"]
                ):
                    raise ArchiveBlocked("Historical WAV object version changed during copy")
        return envelope, artifacts, flags

    def _require_deletion_scope(self):
        if self.copy_published_history or any(
            item.get("body", {}).get("copy_scope") for item in (self.expected or [])
        ):
            raise ArchiveBlocked("Historical COPY-only proof never authorizes WAV deletion")

    def candidate(self, envelope, metadata, flags):
        records = [
            Recording.from_receipt(item["recording"]) for item in envelope["body"]["originals"]
        ]
        end = max(record.ended_at for record in records)
        return Candidate(
            self.environment,
            self.kind,
            self.kind + "_wav",
            envelope["organization"],
            min(record.started_at for record in records),
            end,
            metadata["content_sha256"],
            1,
            "wav",
            max(record.received_at for record in records),
            late_until=end + timedelta(days=7) if self.kind == "direct" else None,
            **flags,
        )

    @contextmanager
    def exclusive(self, candidate):
        self._require_deletion_scope()
        if not self.expected:
            raise ArchiveBlocked("Exact restored WAV metadata required")
        if (
            candidate.environment == "production"
            and os.getenv("WAV_SOURCE_WORK_LEASES_ENABLED") != "true"
        ):
            raise ArchiveBlocked("Production WAV source-owned work leases are not enabled")
        with self.sessions.begin() as db:
            self.db = db
            try:
                db.execute(text("SET LOCAL lock_timeout='150ms'"))
                db.execute(text("SET LOCAL statement_timeout='3s'"))
                reference = self.expected[0]["body"]["reference"]
                if (
                    candidate.environment == "production"
                    or os.getenv("WAV_SOURCE_WORK_LEASES_ENABLED") == "true"
                ):
                    from app.raw_archive.worklease import object_lock_key

                    keys = sorted(
                        {
                            object_lock_key(
                                self.kind, item["recording"]["bucket"], item["recording"]["key"]
                            )
                            for item in self.expected[0]["body"]["originals"]
                        }
                    )
                    if not keys:
                        raise ArchiveBlocked("Exact source WAV work-lease scope is empty")
                    for key in keys:
                        if (
                            db.execute(
                                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}
                            ).scalar_one()
                            is not True
                        ):
                            raise ArchiveBlocked("WAV is in active reader, export or feature use")
                if self.kind == "gateway":
                    from app.models.wav_file import WavFile

                    wav = (
                        db.query(WavFile)
                        .filter_by(id=uuid.UUID(reference[0]))
                        .with_for_update(nowait=True)
                        .one()
                    )
                    from app.models.master import Gateway, Sensor, Zone

                    sensor = (
                        db.query(Sensor)
                        .filter_by(id=wav.sensor_id)
                        .with_for_update(nowait=True)
                        .one()
                    )
                    gateway = (
                        db.query(Gateway)
                        .filter_by(id=sensor.gateway_id)
                        .with_for_update(nowait=True)
                        .one()
                    )
                    db.query(Zone).filter_by(id=gateway.zone_id).with_for_update(nowait=True).one()
                    from app.models.wav_file import WavFeature, WavFeatureVersion

                    db.query(WavFeature).filter_by(
                        wav_file_id=uuid.UUID(reference[0])
                    ).with_for_update(nowait=True).all()
                    db.query(WavFeatureVersion).filter_by(
                        wav_file_id=uuid.UUID(reference[0])
                    ).with_for_update(nowait=True).all()
                else:
                    from app.direct.models import Device, Enrollment, Revision, Segment
                    from app.direct.models import Session as CaptureSession

                    segment = (
                        db.query(Segment)
                        .filter_by(id=reference[0])
                        .with_for_update(nowait=True)
                        .one()
                    )
                    db.query(Revision).filter_by(
                        segment_id=reference[0], revision=reference[1]
                    ).with_for_update(nowait=True).one()
                    db.query(Device).filter_by(id=segment.device_id).with_for_update(
                        nowait=True
                    ).one()
                    db.query(CaptureSession).filter_by(
                        device_id=segment.device_id, id=segment.session_id
                    ).with_for_update(nowait=True).one()
                    db.query(Enrollment).filter_by(device_id=segment.device_id).with_for_update(
                        nowait=True
                    ).all()
                yield
            finally:
                self.db = None

    def absent(self, candidate):
        self._require_deletion_scope()
        absent = []
        for item in self.expected[0]["body"]["originals"]:
            record = Recording.from_receipt(item["recording"])
            absent.append(self.objects.deletion_target(record, item["snapshot"]) is None)
        if any(absent) and not all(absent):
            # A single revision can contain several WAV runs. A crash after one
            # exact version deletion is recoverable by checking each remaining run.
            return False
        return all(absent)

    def inspect(self, candidate):
        self._require_deletion_scope()
        body = self.expected[0]["body"]
        actual = self.catalog.resolve(body["reference"])
        expected = [item["recording"] for item in body["originals"]]
        if [record_json(record) for record in actual] != expected:
            raise ArchiveBlocked("WAV source features or revision changed")
        if self.kind == "gateway":
            organization = self.db.execute(
                text("""SELECT z.organization_id FROM wav_file w JOIN sensor s ON s.id=w.sensor_id
                JOIN gateway g ON g.id=s.gateway_id JOIN zone z ON z.id=g.zone_id WHERE w.id=:id"""),
                {"id": body["reference"][0]},
            ).scalar_one()
        else:
            organization = self.db.execute(
                text(
                    """SELECT d.organization_id FROM direct_segment s JOIN direct_device d ON d.id=s.device_id WHERE s.id=:id"""
                ),
                {"id": body["reference"][0]},
            ).scalar_one()
        if str(organization) != candidate.organization:
            raise ArchiveBlocked("WAV ownership changed")
        # Re-read every archived feature version and object attribute under the
        # same parent/revision locks; a newer feature must get a new archive proof.
        envelope, _, flags = self.metadata(body["reference"])
        for fresh, archived in zip(envelope["body"]["originals"], body["originals"], strict=True):
            record = Recording.from_receipt(archived["recording"])
            absent = self.objects.deletion_target(record, archived["snapshot"]) is None
            if absent and not self.deletion_intent_active:
                raise ArchiveBlocked("WAV vanished without a durable deletion intent")
            # deletion_target independently validates exact VersionId and all
            # attributes for remaining objects; already deleted runs are resumable.
            fresh["snapshot"] = archived["snapshot"]

        def metadata_fingerprint(value):
            from .codec import encode

            value = dict(value, body=dict(value["body"]))
            metadata = dict(value["body"]["metadata"])
            if "feature_versions" in metadata:
                metadata["feature_versions"] = sorted(metadata["feature_versions"], key=encode)
            value["body"]["metadata"] = metadata
            return digest(value)

        if metadata_fingerprint(envelope) != metadata_fingerprint(self.expected[0]):
            raise ArchiveBlocked("Archived WAV metadata, feature versions or source object changed")
        if (
            self.candidate(envelope, {"content_sha256": candidate.content_sha256}, flags)
            != candidate
        ):
            raise ArchiveBlocked("WAV processing, timing or current revision changed")
        return candidate

    def delete(self, candidate):
        self._require_deletion_scope()
        body = self.expected[0]["body"]
        for item in body["originals"]:
            self.checkpoint()
            if not callable(self.deletion_guard):
                raise ArchiveBlocked("Fresh common WAV deletion guard required")
            self.deletion_guard()
            record = Recording.from_receipt(item["recording"])
            self.objects.evict(record, item["snapshot"])

    def complete_eviction(self, candidate):
        """Commit only the retained catalog marker, including crash recovery."""
        self._require_deletion_scope()
        body = self.expected[0]["body"]
        if self.kind == "gateway":
            from app.models.wav_file import WavFile

            wav = self.db.get(WavFile, uuid.UUID(body["reference"][0]))
            if wav.raw_deleted_at is None:
                wav.raw_deleted_at = datetime.now(UTC)
        else:
            from app.direct.models import Revision

            revision = self.db.get(Revision, tuple(body["reference"]))
            if revision.raw_deleted_at is None:
                revision.raw_deleted_at = datetime.now(UTC).timestamp()

    def publish_legacy_receipts(self, envelope, transport, now):
        # Existing authenticated WAV routes continue resolving the original S3
        # keys through the existing read broker. The cold SQL format is additive.
        ledger = RawLedger(self.raw_config.root)
        try:
            for item in envelope["body"]["originals"]:
                record = Recording.from_receipt(item["recording"])
                context = {}
                if envelope["body"].get("copy_scope"):
                    from app.raw_archive.wav_context import receipt_context

                    context["source_identity"] = receipt_context(envelope["body"]["copy_scope"])
                ledger.save(
                    record,
                    "verified",
                    {
                        "recording": item["recording"],
                        "snapshot": item["snapshot"],
                        "remote_key": record.remote_key(self.environment),
                        "destination": transport.identity,
                        "first_verified_at": now.isoformat(),
                        "verified_at": now.isoformat(),
                        **context,
                    },
                )
            group = os.environ.get("TIERED_ARCHIVE_READ_GID")
            state = os.environ.get("TIERED_ARCHIVE_STATE_DIR")
            if group and state:
                from pathlib import Path

                from app.raw_archive.read_catalog import publish

                publish(ledger.db, Path(state), int(group), checkpoint=self.checkpoint)
        finally:
            ledger.close()
