"""Gateway and Direct WAV bundles retain scientific metadata and old URLs."""

import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

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


def row_dict(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


class WavSource:
    def __init__(self, sessions, catalog, objects, kind, environment, raw_config, checkpoint):
        self.sessions, self.catalog, self.objects = sessions, catalog, objects
        self.kind, self.environment, self.raw_config, self.checkpoint = (
            kind,
            environment,
            raw_config,
            checkpoint,
        )
        self.expected, self.db = None, None

    def export(self, reference, folder):
        records = self.catalog.resolve(reference)
        if not records:
            raise ArchiveBlocked("No verified WAVs in revision")
        total = sum(record.size for record in records)
        if total > MAX_BYTES or any(not 0 < record.size <= MAX_BYTES for record in records):
            raise ArchiveBlocked("WAV revision exceeds bounded bundle budget")
        disk_reserve(folder, total * 2)
        with self.sessions() as db:
            if self.kind == "gateway":
                from app.models.wav_file import WavFile

                wav = db.get(WavFile, uuid.UUID(records[0].identity))
                metadata = {
                    "wav": row_dict(wav),
                    "feature": row_dict(wav.feature),
                    "feature_versions": [row_dict(v) for v in wav.feature_versions],
                }
                row = db.execute(
                    text("""SELECT z.organization_id FROM sensor s JOIN gateway g ON g.id=s.gateway_id
                    JOIN zone z ON z.id=g.zone_id WHERE s.id=:id"""),
                    {"id": wav.sensor_id},
                ).one()
                organization, sensor = str(row[0]), str(wav.sensor_id)
                complete = wav.timing_status == "complete"
                headers = {
                    wav.s3_key: {
                        "frames": wav.feature.sample_count,
                        "rate": wav.feature.sample_rate,
                        "channels": 1,
                        "width": 2,
                    }
                }
                projections = db.execute(
                    text("SELECT status='verified' FROM visual_wav WHERE wav_id=:id"),
                    {"id": wav.id},
                ).scalar()
            else:
                from app.direct.models import Device, Revision, Segment
                from app.direct.models import Session as CaptureSession

                segment = db.get(Segment, reference[0])
                revision = db.get(Revision, tuple(reference))
                device = db.get(Device, segment.device_id)
                session = db.get(CaptureSession, (segment.device_id, segment.session_id))
                metadata = {
                    "segment": row_dict(segment),
                    "revision": row_dict(revision),
                    "session": row_dict(session),
                }
                organization, sensor = str(device.organization_id), segment.device_id
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
        artifacts = []
        snapshots = []
        for number, record in enumerate(records):
            self.checkpoint()
            snapshot = self.objects.snapshot(record)
            path = folder / f"original-{number}.wav"
            self.objects.download(record, snapshot, path)
            if checksum(path) != record.sha256 or path.stat().st_size != record.size:
                raise ArchiveBlocked("Source WAV differs from scientific metadata")
            snapshots.append({"recording": record_json(record), "snapshot": snapshot})
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
        at = min(record.started_at for record in records)
        envelope = {
            "identity": digest(reference),
            "organization": organization,
            "sensor": sensor,
            "time_us": epoch_us(at),
            "body": {"reference": reference, "metadata": metadata, "originals": snapshots},
        }
        return (
            envelope,
            artifacts,
            dict(
                complete=bool(complete),
                features_verified=True,
                projections_verified=bool(projections),
                dependencies_complete=True,
            ),
        )

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
        if not self.expected:
            raise ArchiveBlocked("Exact restored WAV metadata required")
        with self.sessions.begin() as db:
            self.db = db
            try:
                db.execute(text("SET LOCAL lock_timeout='150ms'"))
                db.execute(text("SET LOCAL statement_timeout='3s'"))
                reference = self.expected[0]["body"]["reference"]
                if self.kind == "gateway":
                    from app.models.wav_file import WavFile

                    db.query(WavFile).filter_by(id=uuid.UUID(reference[0])).with_for_update(
                        nowait=True
                    ).one()
                    from app.models.wav_file import WavFeature, WavFeatureVersion

                    db.query(WavFeature).filter_by(
                        wav_file_id=uuid.UUID(reference[0])
                    ).with_for_update(nowait=True).all()
                    db.query(WavFeatureVersion).filter_by(
                        wav_file_id=uuid.UUID(reference[0])
                    ).with_for_update(nowait=True).all()
                else:
                    from app.direct.models import Segment

                    db.query(Segment).filter_by(id=reference[0]).with_for_update(nowait=True).one()
                yield
            finally:
                self.db = None

    def absent(self, candidate):
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
        return candidate

    def delete(self, candidate):
        body = self.expected[0]["body"]
        for item in body["originals"]:
            self.checkpoint()
            record = Recording.from_receipt(item["recording"])
            self.objects.evict(record, item["snapshot"])
        if self.kind == "gateway":
            from app.models.wav_file import WavFile

            wav = self.db.get(WavFile, uuid.UUID(body["reference"][0]))
            wav.raw_deleted_at = datetime.now(UTC)
        else:
            from app.direct.models import Revision

            revision = self.db.get(Revision, tuple(body["reference"]))
            revision.raw_deleted_at = datetime.now(UTC).timestamp()

    def publish_legacy_receipts(self, envelope, transport, now):
        # Existing authenticated WAV routes continue resolving the original S3
        # keys through the existing read broker. The cold SQL format is additive.
        ledger = RawLedger(self.raw_config.root)
        try:
            for item in envelope["body"]["originals"]:
                record = Recording.from_receipt(item["recording"])
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
                    },
                )
        finally:
            ledger.close()
