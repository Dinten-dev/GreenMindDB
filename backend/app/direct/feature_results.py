"""Local result persistence primitives. No autonomous worker is enabled here."""

import hashlib
import hmac
import math
import os
import re
import time
import uuid

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.direct.database import transaction
from app.direct.models import FeatureJob, FeatureResult
from app.services.signal_feature_contract import (
    CONTRACT_VERSION,
    canonical_sha256,
    effective_parameter_sha256,
    implementation_identity,
)
from app.services.signal_quality_v1 import classify_quality_v1

_HISTORICAL_CLASSIFIERS = {"signal-contract-v1": classify_quality_v1}

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_INPUTS = {
    "device_id",
    "session_id",
    "segment_id",
    "revision",
    "manifest_sha256",
    "wav_key",
    "wav_sha256",
    "first_frame",
    "frame_count",
    "channel_index",
    "channel_label",
    "extractor_version",
    "parameter_sha256",
    "calibration_sha256",
    "quality_context_sha256",
    "calculator_source_sha256",
}
_PAYLOAD = {
    "contract_version",
    "calculator_source_sha256",
    "sample_rate_hz",
    "sample_count",
    "calibration",
    "calibration_sha256",
    "raw_clip_bounds",
    "features",
    "anomaly_index",
    "units",
    "continuity",
    "source_quality_counts",
    "coverage_ratio",
    "quality",
}


class ResultConflict(ValueError):
    pass


def validate_result(inputs, payload, *, require_current=True):
    if set(inputs) != _INPUTS or set(payload) != _PAYLOAD:
        raise ValueError("Incomplete or unknown result fields")
    for key in (
        "manifest_sha256",
        "wav_sha256",
        "parameter_sha256",
        "calibration_sha256",
        "quality_context_sha256",
        "calculator_source_sha256",
    ):
        if not isinstance(inputs[key], str) or not _SHA.fullmatch(inputs[key]):
            raise ValueError("Invalid identity digest")
    for key in (
        "device_id",
        "session_id",
        "segment_id",
        "wav_key",
        "channel_label",
        "extractor_version",
    ):
        if not isinstance(inputs[key], str) or not inputs[key] or len(inputs[key]) > 1024:
            raise ValueError("Invalid source identity")
    for key in ("revision", "first_frame", "frame_count", "channel_index"):
        if type(inputs[key]) is not int or inputs[key] < (
            1 if key in ("revision", "frame_count") else 0
        ):
            raise ValueError("Invalid source interval")
    if (
        payload["sample_count"] != inputs["frame_count"]
        or payload["calibration_sha256"] != inputs["calibration_sha256"]
        or canonical_sha256(payload["calibration"]) != inputs["calibration_sha256"]
        or payload["calculator_source_sha256"] != inputs["calculator_source_sha256"]
    ):
        raise ValueError("Result identity mismatch")
    if (
        canonical_sha256(
            {
                "continuity": payload["continuity"],
                "source_quality_counts": payload["source_quality_counts"],
                "coverage_ratio": payload["coverage_ratio"],
                "observations": payload["quality"]["observation_status"],
            }
        )
        != inputs["quality_context_sha256"]
    ):
        raise ValueError("Quality context mismatch")
    version = payload["contract_version"]
    if (
        version not in _HISTORICAL_CLASSIFIERS
        or inputs["extractor_version"] != version
        or (
            require_current
            and (
                version != CONTRACT_VERSION
                or inputs["parameter_sha256"] != effective_parameter_sha256(payload)
            )
        )
    ):
        raise ValueError("Effective extractor identity mismatch")
    calibration = payload["calibration"]
    if not isinstance(calibration, dict) or set(calibration) != {
        "status",
        "scale",
        "offset",
        "unit",
        "source",
        "channel",
        "encoding",
        "validity",
    }:
        raise ValueError("Invalid calibration descriptor fields")
    if calibration.get("channel") != inputs["channel_label"]:
        raise ValueError("Calibration channel mismatch")
    raw_bounds = payload["raw_clip_bounds"]
    if (
        not isinstance(raw_bounds, list)
        or len(raw_bounds) != 2
        or any(type(value) is not int for value in raw_bounds)
    ):
        raise ValueError("Invalid clipping bounds")
    bounds = tuple(raw_bounds)
    signed_bounds = {(-32768, 32767): 16, (0, 32767): 16, (-8388608, 8388607): 24}
    unsigned_bounds = {(0, 65535): 16, (0, 16777215): 24}
    permitted = (
        signed_bounds
        if calibration["encoding"] == "signed"
        else unsigned_bounds
        if calibration["encoding"] == "unsigned"
        else {}
    )
    if bounds not in permitted:
        raise ValueError("Unapproved encoding clipping bounds")
    import numpy as np

    from app.services.signal_feature_contract import decode_values

    decode_values(
        np.array([0], dtype=np.int32),
        bits=permitted[bounds],
        encoding=calibration["encoding"],
        clip_bounds=bounds,
        descriptor=calibration,
    )
    if set(payload["units"]) != {
        "amplitude",
        "spectral_energy",
        "frequency",
        "time",
        "ratio",
    } or payload["units"] != {
        "amplitude": payload["calibration"]["unit"],
        "spectral_energy": payload["calibration"]["unit"] + "^2",
        "frequency": "Hz",
        "time": "s",
        "ratio": "dimensionless",
    }:
        raise ValueError("Invalid units")
    feature_keys = {
        "mean",
        "median",
        "rms",
        "standard_deviation",
        "minimum",
        "maximum",
        "quantiles",
        "outlier_count",
        "outlier_ratio",
        "clipping_count",
        "clipping_ratio",
        "flatline_count",
        "flatline_seconds",
        "spectral_energy_total",
        "dominant_frequency_hz",
        "spectral_bands",
    }
    if set(payload["features"]) != feature_keys or set(payload["features"]["quantiles"]) != {
        "p01",
        "p05",
        "p25",
        "p75",
        "p95",
        "p99",
    }:
        raise ValueError("Incomplete numerical features")
    if (
        type(payload["sample_rate_hz"]) is not int
        or payload["sample_rate_hz"] <= 0
        or type(payload["sample_count"]) is not int
        or payload["sample_count"] <= 0
        or type(payload["anomaly_index"]) is not int
        or not 0 <= payload["anomaly_index"] < payload["sample_count"]
    ):
        raise ValueError("Invalid sampling identity")
    features = payload["features"]
    bands = {
        "ultra_low_0_003_0_04_hz",
        "very_low_0_04_0_15_hz",
        "low_0_15_0_5_hz",
        "mid_0_5_1_hz",
        "high_1_10_hz",
        "very_high_10_50_hz",
    }
    if payload["sample_count"] >= 16 and payload["sample_rate_hz"] / 2 > 50:
        bands.add("noise_50_nyquist_hz")
    if set(features["spectral_bands"]) != bands:
        raise ValueError("Invalid spectral bands")
    values = (
        [value for key, value in features.items() if key not in {"quantiles", "spectral_bands"}]
        + list(features["quantiles"].values())
        + list(features["spectral_bands"].values())
    )
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ValueError("Feature values must be finite numbers")
    for key in ("outlier_count", "clipping_count", "flatline_count"):
        if type(features[key]) is not int or not 0 <= features[key] <= payload["sample_count"]:
            raise ValueError("Invalid feature count")
    if (
        any(not 0 <= features[key] <= 1 for key in ("outlier_ratio", "clipping_ratio"))
        or any(
            features[key] < 0
            for key in (
                "rms",
                "standard_deviation",
                "flatline_seconds",
                "spectral_energy_total",
                "dominant_frequency_hz",
            )
        )
        or any(value < 0 for value in features["spectral_bands"].values())
    ):
        raise ValueError("Invalid feature ranges")
    quality = payload["quality"]
    if quality != _HISTORICAL_CLASSIFIERS[version](
        payload["features"],
        payload["continuity"],
        payload["source_quality_counts"],
        payload["coverage_ratio"],
        quality["observation_status"],
    ):
        raise ValueError("Quality classification mismatch")
    canonical_sha256(inputs)
    canonical_sha256(payload)


def persist_result(engine, inputs, payload, *, guard=None):
    """Persist once, then reread and verify; caller must hold source/lease fences."""
    validate_result(inputs, payload)
    identity, digest = canonical_sha256(inputs), canonical_sha256(payload)
    with transaction(engine) as db:
        if guard is not None:
            guard(db)
        insert = (
            sqlite_insert
            if engine.dialect.name == "sqlite"
            else pg_insert
            if engine.dialect.name == "postgresql"
            else None
        )
        if insert is None:
            raise ValueError("Unsupported database dialect")
        db.execute(
            insert(FeatureResult)
            .values(
                id=str(uuid.uuid4()),
                segment_id=inputs["segment_id"],
                revision=inputs["revision"],
                manifest_sha256=inputs["manifest_sha256"],
                input_sha256=identity,
                inputs=inputs,
                payload=payload,
                payload_sha256=digest,
                created_at=time.time(),
                verified_at=None,
            )
            .on_conflict_do_nothing(index_elements=["input_sha256"])
        )
        row = db.execute(
            select(FeatureResult).where(FeatureResult.input_sha256 == identity)
        ).scalar_one()
        if (
            not hmac.compare_digest(row.payload_sha256, digest)
            or row.inputs != inputs
            or row.payload != payload
        ):
            raise ResultConflict("Different result for identical complete inputs")
        result_id = row.id
    # Independent transaction, read persisted values, then compare all digests.
    with transaction(engine) as db:
        if guard is not None:
            guard(db)
        row = db.execute(
            select(FeatureResult).where(FeatureResult.id == result_id).with_for_update()
        ).scalar_one()
        validate_result(row.inputs, row.payload)
        if (
            canonical_sha256(row.inputs) != identity
            or canonical_sha256(row.payload) != digest
            or row.payload_sha256 != digest
        ):
            raise ResultConflict("Persisted result failed readback")
        if row.verified_at is None:
            row.verified_at = time.time()
    return result_id


def claim_job(engine, job_id, *, now=None, lease_seconds=60, max_attempts=5):
    now = time.time() if now is None else now
    if not 1 <= lease_seconds <= 3600 or not 1 <= max_attempts <= 100:
        raise ValueError("Invalid claim bounds")
    token = str(uuid.uuid4())
    with transaction(engine) as db:
        statement = (
            update(FeatureJob)
            .where(
                FeatureJob.id == job_id,
                FeatureJob.attempts < max_attempts,
                FeatureJob.retry_at <= now,
                (FeatureJob.status == "pending")
                | ((FeatureJob.status == "claimed") & (FeatureJob.claim_until <= now)),
            )
            .values(
                status="claimed",
                claim_token=token,
                claim_until=now + lease_seconds,
                attempts=FeatureJob.attempts + 1,
            )
        )
        if db.execute(statement).rowcount != 1:
            return None
    return token


def enqueue_sealed_page(engine, *, after="", limit=20):
    """Keyset discovery; creates jobs only for sealed published revisions."""
    from app.direct.models import Revision, Segment

    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Invalid page bound")
    with transaction(engine) as db:
        rows = (
            db.query(Segment.id, Segment.published_revision, Revision.manifest_sha256)
            .join(
                Revision,
                (Revision.segment_id == Segment.id)
                & (Revision.revision == Segment.published_revision),
            )
            .filter(
                Segment.sealed.is_(True),
                Segment.id > after,
                Segment.published_revision == Segment.revision,
                Revision.raw_deleted_at.is_(None),
            )
            .order_by(Segment.id)
            .limit(limit)
            .all()
        )
        insert = (
            sqlite_insert
            if engine.dialect.name == "sqlite"
            else pg_insert
            if engine.dialect.name == "postgresql"
            else None
        )
        if insert is None:
            raise ValueError("Unsupported database dialect")
        for segment_id, revision, manifest_sha256 in rows:
            target = {
                "segment_id": segment_id,
                "revision": revision,
                "manifest_sha256": manifest_sha256,
                "implementation": implementation_identity(),
            }
            db.execute(
                insert(FeatureJob)
                .values(
                    id=str(uuid.uuid4()),
                    target_sha256=canonical_sha256(target),
                    target=target,
                    status="pending",
                    retry_at=0,
                    attempts=0,
                    created_at=time.time(),
                )
                .on_conflict_do_nothing(index_elements=["target_sha256"])
            )
        return rows[-1][0] if rows else None


def _source_snapshot(engine, target, *, locked_db=None):
    """Snapshot scientific source and full original chunk identities in a short transaction."""
    import copy
    from contextlib import nullcontext

    from app.direct.capture_assignment import manifest_assignment, split
    from app.direct.models import Chunk, PayloadReference, Piece, Revision, Segment
    from app.direct.models import Session as CaptureSession

    with nullcontext(locked_db) if locked_db is not None else transaction(engine) as db:
        lock = locked_db is not None
        segment = db.get(Segment, target["segment_id"], with_for_update=lock)
        revision = db.get(
            Revision, (target["segment_id"], target["revision"]), with_for_update=lock
        )
        if (
            segment is None
            or revision is None
            or not segment.sealed
            or not revision.verified_at
            or revision.raw_deleted_at is not None
            or segment.revision != target["revision"]
            or segment.published_revision != target["revision"]
            or revision.manifest_sha256 != target["manifest_sha256"]
            or canonical_sha256(revision.manifest) != revision.manifest_sha256
        ):
            raise ValueError("Sealed source revision changed")
        session = db.get(
            CaptureSession, (segment.device_id, segment.session_id), with_for_update=lock
        )
        config, capture = split(session.config)
        manifest = copy.deepcopy(revision.manifest)
        if (
            manifest["device_id"],
            manifest["session_id"],
            manifest["revision"],
            manifest["config"],
            manifest["source_mode"],
            manifest_assignment(manifest),
        ) != (
            segment.device_id,
            segment.session_id,
            revision.revision,
            config,
            session.mode,
            capture,
        ):
            raise ValueError("Source configuration or acquisition identity changed")
        if (manifest["first_frame"], manifest["end_frame"]) != (
            segment.first_frame,
            segment.end_frame,
        ):
            raise ValueError("Segment boundaries changed")
        query = (
            db.query(Piece, Chunk)
            .join(Chunk, Piece.chunk_id == Chunk.id)
            .filter(Piece.segment_id == segment.id)
            .order_by(Chunk.first_frame, Chunk.id)
        )
        pieces = (query.with_for_update() if lock else query).all()
        if not pieces or len(pieces) > 1200:
            raise ValueError("Chunk count exceeds extraction budget")
        output = []
        for piece, chunk in pieces:
            if (chunk.device_id, chunk.session_id) != (segment.device_id, segment.session_id):
                raise ValueError("Foreign source chunk")
            ref = (
                db.get(PayloadReference, chunk.id, with_for_update=lock)
                if chunk.payload is None
                else None
            )
            # Detached objects remain usable after closing the short transaction.
            if chunk.payload is None and ref is None:
                raise ValueError("Original source reference unavailable")
            if ref is not None:
                if (
                    ref.version != 1
                    or not isinstance(ref.parts, list)
                    or not 1 <= len(ref.parts) <= 16
                ):
                    raise ValueError("Invalid original reference")
                for part in ref.parts:
                    if set(part) != {
                        "segment_id",
                        "revision",
                        "manifest_sha256",
                        "key",
                        "sha256",
                        "first_frame",
                        "run_frames",
                        "run_offset",
                        "chunk_offset",
                        "frame_count",
                    }:
                        raise ValueError("Unknown original reference fields")
                    source_segment = db.get(Segment, part["segment_id"], with_for_update=lock)
                    source_revision = db.get(
                        Revision, (part["segment_id"], part["revision"]), with_for_update=lock
                    )
                    source_piece = db.get(
                        Piece, (part["segment_id"], chunk.id), with_for_update=lock
                    )
                    if source_piece is None or (
                        source_piece.payload_offset,
                        source_piece.frame_count,
                    ) != (part["chunk_offset"], part["frame_count"]):
                        raise ValueError("Original reference is not bound to source piece")
                    if (
                        source_segment is None
                        or source_revision is None
                        or not source_revision.verified_at
                        or source_revision.raw_deleted_at is not None
                        or (source_segment.device_id, source_segment.session_id)
                        != (segment.device_id, segment.session_id)
                        or source_revision.manifest_sha256 != part["manifest_sha256"]
                        or canonical_sha256(source_revision.manifest) != part["manifest_sha256"]
                    ):
                        raise ValueError("Foreign or changed original reference revision")
                    source_manifest = source_revision.manifest
                    if (
                        source_manifest["device_id"],
                        source_manifest["session_id"],
                        source_manifest["config"],
                        source_manifest["revision"],
                    ) != (segment.device_id, segment.session_id, config, part["revision"]):
                        raise ValueError("Original reference configuration mismatch")
                    matches = [
                        run
                        for run in source_manifest["runs"]
                        if (run["key"], run["sha256"], run["first_frame"], run["frame_count"])
                        == (part["key"], part["sha256"], part["first_frame"], part["run_frames"])
                    ]
                    if len(matches) != 1:
                        raise ValueError("Original reference WAV does not belong to revision")
            db.expunge(piece)
            db.expunge(chunk)
            if ref is not None:
                db.expunge(ref)
            output.append((piece, chunk, ref))
        fingerprint = {
            "segment": {
                key: getattr(segment, key)
                for key in (
                    "id",
                    "device_id",
                    "session_id",
                    "first_frame",
                    "end_frame",
                    "revision",
                    "published_revision",
                    "sealed",
                )
            },
            "manifest": manifest,
            "mode": session.mode,
            "capture": capture,
            "pieces": [
                {
                    "chunk_id": c.id,
                    "device_id": c.device_id,
                    "session_id": c.session_id,
                    "sequence": c.sequence,
                    "first_frame": c.first_frame,
                    "frame_count": c.frame_count,
                    "payload_sha256": c.payload_sha256,
                    "payload_bytes": c.payload_bytes,
                    "inline_sha256": hashlib.sha256(c.payload).hexdigest()
                    if c.payload is not None
                    else None,
                    "offset": p.payload_offset,
                    "piece_frames": p.frame_count,
                    "reference": {"version": r.version, "parts": r.parts} if r else None,
                }
                for p, c, r in output
            ],
        }
    return manifest, output, canonical_sha256(fingerprint)


def process_claim(
    engine, store, settings, job_id, token, *, max_bytes=32_000_000, max_files=32, max_seconds=90
):
    """Verify bounded originals and calculate a claimed sealed revision under source leases."""
    from contextlib import ExitStack

    from app.direct.features import contract_channel_payloads, resolve_calibration
    from app.direct.payloads import WavReader, read_payload_snapshot
    from app.raw_archive.remote_lease import catalog_lease
    from app.raw_archive.worklease import enabled as worklease_enabled
    from app.raw_archive.worklease import shared_wav_leases

    if not 1 <= max_bytes <= 64_000_000 or not 1 <= max_files <= 64 or not 1 <= max_seconds <= 120:
        raise ValueError("Invalid extraction bounds")
    if settings.environment != "development" and (
        not worklease_enabled()
        or not (
            os.environ.get("RAW_ARCHIVE_COORDINATION_DIR")
            or os.environ.get("RAW_ARCHIVE_LEASE_HOST")
        )
    ):
        raise ValueError("Authoritative source leases are required")
    started = time.monotonic()

    def checkpoint():
        check_lease()
        if time.monotonic() - started > max_seconds:
            raise TimeoutError("Feature batch time budget exceeded")

    def current_claim(db):
        job = db.get(FeatureJob, job_id, with_for_update=True)
        if (
            job is None
            or job.status != "claimed"
            or job.claim_token != token
            or job.claim_until <= time.time()
            or canonical_sha256(job.target) != job.target_sha256
        ):
            raise ValueError("Feature claim expired or changed")
        return dict(job.target)

    with catalog_lease() as check_lease:
        with transaction(engine) as db:
            target = current_claim(db)
        if target.get("implementation") != implementation_identity():
            raise ValueError("Extractor implementation changed since claim")
        manifest, pieces, before = _source_snapshot(engine, target)
        config, runs = manifest["config"], manifest["runs"]
        if not 1 <= len(runs) <= max_files:
            raise ValueError("Run count exceeds extraction budget")
        frame_bytes = config["channels"] * config["sample_bits"] // 8
        if frame_bytes <= 0 or sum(c.payload_bytes for _, c, _ in pieces) > max_bytes:
            raise ValueError("Chunk bytes exceed extraction budget")
        wavs = {}
        for run in runs:
            wavs[run["key"]] = (run["sha256"], run["frame_count"])
        for _, _, ref in pieces:
            if ref is not None:
                for part in ref.parts:
                    identity = (part["sha256"], part["run_frames"])
                    if part["key"] in wavs and wavs[part["key"]] != identity:
                        raise ValueError("Conflicting WAV reference")
                    wavs[part["key"]] = identity
        if (
            len(wavs) > max_files
            or sum(44 + count * frame_bytes for _, count in wavs.values()) > max_bytes
        ):
            raise ValueError("WAV bytes exceed extraction budget")
        with ExitStack() as stack:
            stack.enter_context(shared_wav_leases("direct", settings.s3_bucket, wavs))
            reader = WavReader(store.get, cache_bytes=min(max_bytes, 8 * 1024**2))
            bodies, gaps, cursor = [], [], manifest["first_frame"]
            for run in runs:
                checkpoint()
                first, count = run["first_frame"], run["frame_count"]
                if (
                    type(first) is not int
                    or type(count) is not int
                    or first < cursor
                    or count <= 0
                    or first + count > manifest["end_frame"]
                ):
                    raise ValueError("Invalid Direct run interval")
                if first > cursor:
                    gaps.append([cursor, first])
                body = reader.read_range(
                    run, config, manifest["device_id"], manifest["session_id"], 0, count
                )
                bodies.append(body)
                cursor = first + count
            if cursor < manifest["end_frame"]:
                gaps.append([cursor, manifest["end_frame"]])
            if gaps != manifest["missing_frame_ranges"]:
                raise ValueError("Segment gap intervals differ")
            # Every original chunk is independently SHA-checked, including parts
            # that extend outside this segment. Every segment byte matches a run.
            next_frame = manifest["first_frame"]
            for piece, chunk, ref in pieces:
                checkpoint()
                payload = read_payload_snapshot(chunk, ref, config, reader)
                first = chunk.first_frame + piece.payload_offset
                end = first + piece.frame_count
                if (
                    piece.payload_offset < 0
                    or piece.frame_count <= 0
                    or piece.payload_offset + piece.frame_count > chunk.frame_count
                    or first < next_frame
                ):
                    raise ValueError("Invalid or overlapping source piece")
                matching = [
                    i
                    for i, run in enumerate(runs)
                    if run["first_frame"] <= first
                    and end <= run["first_frame"] + run["frame_count"]
                ]
                if len(matching) != 1:
                    raise ValueError("Source piece is not covered by exactly one run")
                index = matching[0]
                run_offset = (first - runs[index]["first_frame"]) * frame_bytes
                source_offset = piece.payload_offset * frame_bytes
                length = piece.frame_count * frame_bytes
                if (
                    payload[source_offset : source_offset + length]
                    != bodies[index][run_offset : run_offset + length]
                ):
                    raise ValueError("Original chunk and WAV differ")
                next_frame = end
            expected_frames = sum(r["frame_count"] for r in runs)
            if sum(p.frame_count for p, _, _ in pieces) != expected_frames:
                raise ValueError("Original chunks do not cover all run frames")
            segment_frames = manifest["end_frame"] - manifest["first_frame"]
            if segment_frames <= 0 or expected_frames > segment_frames:
                raise ValueError("Invalid segment coverage")
            coverage = expected_frames / segment_frames
            flags = dict.fromkeys(
                ("valid", "lead_off", "rail_high", "rail_low", "jump", "recovery")
            )
            continuity = {"frame_gap_count": len(gaps), "source_dropped_samples_delta": None}
            observed = dict(
                coverage=True, continuity=True, source_flags=False, dropped_samples=False
            )
            quality_hash = canonical_sha256(
                {
                    "continuity": continuity,
                    "source_quality_counts": flags,
                    "coverage_ratio": coverage,
                    "observations": observed,
                }
            )
            descriptors = [resolve_calibration(config, i) for i in range(config["channels"])]
            result_ids = []
            for run, body in zip(runs, bodies, strict=True):
                channel_results = contract_channel_payloads(
                    body,
                    config,
                    descriptors,
                    continuity=continuity,
                    source_quality=flags,
                    coverage_ratio=coverage,
                    observations=observed,
                )
                for channel in channel_results:
                    checkpoint()
                    with transaction(engine) as db:
                        if current_claim(db) != target:
                            raise ValueError("Feature claim changed")
                    if _source_snapshot(engine, target)[2] != before:
                        raise ValueError("Source changed during extraction")
                    payload = channel["signal"]
                    inputs = dict(
                        device_id=manifest["device_id"],
                        session_id=manifest["session_id"],
                        segment_id=target["segment_id"],
                        revision=target["revision"],
                        manifest_sha256=target["manifest_sha256"],
                        wav_key=run["key"],
                        wav_sha256=run["sha256"],
                        first_frame=run["first_frame"],
                        frame_count=run["frame_count"],
                        channel_index=channel["channel_index"],
                        channel_label=channel["channel_label"],
                        extractor_version=CONTRACT_VERSION,
                        parameter_sha256=effective_parameter_sha256(payload),
                        calibration_sha256=payload["calibration_sha256"],
                        quality_context_sha256=quality_hash,
                        calculator_source_sha256=payload["calculator_source_sha256"],
                    )

                    def source_guard(db):
                        if (
                            current_claim(db) != target
                            or _source_snapshot(engine, target, locked_db=db)[2] != before
                        ):
                            raise ValueError("Claim or source changed before result commit")

                    result_ids.append(persist_result(engine, inputs, payload, guard=source_guard))
            if _source_snapshot(engine, target)[2] != before:
                raise ValueError("Source changed after extraction")
            with transaction(engine) as db:
                current_claim(db)
                db.execute(
                    update(FeatureJob)
                    .where(FeatureJob.id == job_id, FeatureJob.claim_token == token)
                    .values(status="done", claim_token=None, claim_until=None)
                )
            return result_ids


def run_feature_batch(engine, store, settings, *, after="", max_jobs=2):
    """Discover and process at most a small number of recoverable claims."""
    from sqlalchemy import inspect as inspect_schema

    if type(max_jobs) is not int or not 1 <= max_jobs <= 10:
        raise ValueError("Invalid feature job limit")
    schema = inspect_schema(engine)
    for model in (FeatureJob, FeatureResult):
        if not schema.has_table(model.__tablename__) or {
            column.name for column in model.__table__.columns
        } != {column["name"] for column in schema.get_columns(model.__tablename__)}:
            raise ValueError("Direct feature schema is absent or incompatible")
    for model, unique_column in ((FeatureJob, "target_sha256"), (FeatureResult, "input_sha256")):
        if not any(
            item.get("column_names") == [unique_column]
            for item in schema.get_unique_constraints(model.__tablename__)
        ):
            raise ValueError("Direct feature unique identity constraint is missing")
    if engine.dialect.name == "postgresql":
        from sqlalchemy import text

        foreign = schema.get_foreign_keys("direct_feature_result")
        if not any(
            item.get("referred_table") == "direct_revision"
            and item.get("constrained_columns") == ["segment_id", "revision"]
            and item.get("referred_columns") == ["segment_id", "revision"]
            for item in foreign
        ):
            raise ValueError("Direct feature revision foreign key is missing")
        with engine.connect() as connection:
            trigger = connection.execute(
                text(
                    """SELECT t.tgenabled, p.prosrc FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND c.relname='direct_feature_result' AND t.tgname='direct_feature_result_immutable' AND NOT t.tgisinternal"""
                )
            ).one_or_none()
            expected = """BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'direct result is append-only'; END IF;
 IF OLD.verified_at IS NOT NULL OR NEW.verified_at IS NULL OR
    (to_jsonb(OLD) - 'verified_at') IS DISTINCT FROM (to_jsonb(NEW) - 'verified_at') THEN
   RAISE EXCEPTION 'direct result is immutable';
 END IF;
 RETURN NEW;
END"""
            if (
                trigger is None
                or trigger[0] not in ("O", "A")
                or " ".join(trigger[1].split()) != " ".join(expected.split())
            ):
                raise ValueError("Direct feature immutable trigger differs")
    cursor = enqueue_sealed_page(engine, after=after, limit=20)
    with transaction(engine) as db:
        now = time.time()
        ids = [
            row[0]
            for row in db.query(FeatureJob.id)
            .filter(
                FeatureJob.retry_at <= now,
                FeatureJob.attempts < 5,
                (FeatureJob.status == "pending")
                | ((FeatureJob.status == "claimed") & (FeatureJob.claim_until <= now)),
            )
            .order_by(FeatureJob.created_at, FeatureJob.id)
            .limit(max_jobs)
            .all()
        ]
    processed = 0
    for job_id in ids:
        token = claim_job(engine, job_id, lease_seconds=120)
        if token is None:
            continue
        try:
            process_claim(engine, store, settings, job_id, token, max_seconds=90)
            processed += 1
        except Exception as error:
            with transaction(engine) as db:
                job = db.get(FeatureJob, job_id)
                if job is not None and job.status == "claimed" and job.claim_token == token:
                    job.status = "failed" if job.attempts >= 5 else "pending"
                    job.retry_at = time.time() + min(3600, 30 * (2 ** max(0, job.attempts - 1)))
                    job.claim_token = None
                    job.claim_until = None
                    job.error = type(error).__name__[:128]
    return cursor, processed
