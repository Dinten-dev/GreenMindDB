"""Quarantined Direct original context using the existing AES/Parquet pipeline.

No source mutation, scheduler change, reference repair or acceptance is made.
A Root-private prepared bundle can be published using normal pipeline.publish.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.raw_archive.coordination import bound_job_lock
from app.raw_archive.policy import ArchiveBlocked, checksum
from app.raw_archive.remote_lease import require_bound_source_lease

from . import backup, codec, parquet, pipeline
from .policy import Candidate

CAPTURE_ROOT = Path(
    "/var/lib/greenmind/production-direct-original-read-a798aabe313d41d680af3feb450cadd5"
)
CAPTURE_FILES = {
    "original-context.jsonl": (
        1464215,
        "faa923182331cadeca82aab1cfe131803ae846f165d9a3bc6386c408816fbe9c",
    ),
    "manifest.jsonl": (2091, "8771225be8e3802a8f361450885448af89d3edc9164bae02d0474076b45d175b"),
    "dependency-pins.jsonl": (
        849,
        "851d17f98133edf8dde5e86b81f920352f832462048e5fa0f3508e069833e6b8",
    ),
    "detached-reconstruction-candidates.jsonl": (
        607139,
        "fd04a560ea03c3a3a0f1901d50348cba365fea3e6b094731094403e04c6e167a",
    ),
    "dependency-revisions.jsonl": (
        7414,
        "e7ebf83451511008d212cb97cc1875a0daca879ebe3c56d42b10eaa38e6fd64a",
    ),
}
SOURCE_ROWS_SHA = "1e649ace89335cd49e489c24ceba7d8b16128a351f317d9c1553247a4a95f30f"
DEPENDENCY_SHA = "be8f12b7122e4cf1950f7f0d312753c5dd7a53922e6d8c3430854832b5692c99"
REVISION_SHA = "68805ed41c5eb5c28204e82b97d008891cfc59288fd706052b49729ec14c45d5"
SOURCE_CHUNKS = 601
SOURCE_ORIGINAL_BYTES = 456760
MAX_WAV = 5 * 1024**2
MAX_TOTAL = 16 * 1024**2
SYSTEM_ORG = "00000000-0000-0000-0000-000000000000"


def need(value, reason):
    if not value:
        raise ArchiveBlocked(reason)


def private_dir(path):
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path, "Exact private Direct directory required")
    info = path.lstat()
    need(
        stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700,
        "Root-private Direct directory required",
    )
    for parent in path.parents:
        item = parent.lstat()
        need(
            stat.S_ISDIR(item.st_mode) and item.st_uid == 0 and not item.st_mode & 0o022,
            "Direct directory parent differs",
        )
    return path


def _read_exact(path, size, sha, *, checkpoint):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    digest = hashlib.sha256()
    chunks = []
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        need(
            stat.S_ISREG(before.st_mode)
            and before.st_uid == 0
            and before.st_nlink == 1
            and stat.S_IMODE(before.st_mode) == 0o600
            and before.st_size == size,
            "Pinned Direct capture file differs",
        )
        while block := stream.read(65536):
            checkpoint()
            digest.update(block)
            chunks.append(block)
        after = os.fstat(stream.fileno())
    need(
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        and digest.hexdigest() == sha,
        "Direct capture file changed",
    )
    return b"".join(chunks)


def _lines(raw, maximum):
    need(type(raw) is bytes and raw.endswith(b"\n"), "Complete Direct JSONL required")
    lines = raw.splitlines()
    need(
        0 < len(lines) <= maximum and all(0 < len(line) <= 1024**2 for line in lines),
        "Direct JSONL row bound differs",
    )
    try:
        return [codec.decode(line) for line in lines]
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise ArchiveBlocked("Direct JSONL encoding differs") from exc


def inspect_capture(*, checkpoint):
    """Verify all five actual frozen files and the known source-derived closure."""
    root = private_dir(CAPTURE_ROOT)
    folder = private_dir(root / "evidence")
    data = {}
    for name, (size, sha) in CAPTURE_FILES.items():
        data[name] = _read_exact(folder / name, size, sha, checkpoint=checkpoint)
    rows = _lines(data["original-context.jsonl"], SOURCE_CHUNKS)
    manifests = _lines(data["manifest.jsonl"], 1)
    pin_rows = _lines(data["dependency-pins.jsonl"], 1)
    candidates = _lines(data["detached-reconstruction-candidates.jsonl"], SOURCE_CHUNKS)
    revisions = _lines(data["dependency-revisions.jsonl"], 16)
    need(
        len(rows) == len(candidates) == SOURCE_CHUNKS and len(manifests) == len(pin_rows) == 1,
        "Direct captured chunk count differs",
    )
    content = hashlib.sha256()
    total = 0
    times = []
    organizations = set()
    sensors = set()
    for item in rows:
        checkpoint()
        need(
            type(item) is dict
            and set(item) == {"identity", "organization", "sensor", "time_us", "body"}
            and type(item["time_us"]) is int
            and type(item["body"]) is dict
            and set(item["body"]) == {"row", "context"},
            "Direct original row envelope differs",
        )
        context = item["body"]["context"]
        need(
            type(context) is dict and type(context.get("original_payload")) is bytes,
            "Direct original bytes absent",
        )
        total += len(context["original_payload"])
        times.append(item["time_us"])
        organizations.add(item["organization"])
        sensors.add(item["sensor"])
        raw = codec.encode(item)
        content.update(len(raw).to_bytes(8, "big") + raw)
    need(
        total == SOURCE_ORIGINAL_BYTES and content.hexdigest() == SOURCE_ROWS_SHA,
        "Direct original chunk digest differs",
    )
    manifest = manifests[0]
    need(
        type(manifest) is dict
        and hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == REVISION_SHA,
        "Direct revision manifest differs",
    )
    need(codec.digest(revisions) == DEPENDENCY_SHA, "Direct neighboring revision closure differs")
    pins = pin_rows[0]
    need(type(pins) is dict and len(pins) == 3, "Exactly three original WAV dependencies required")
    for key, pin in pins.items():
        need(
            type(key) is str
            and key.startswith("direct/")
            and key.endswith(".wav")
            and ".." not in key.split("/")
            and type(pin) is dict
            and set(pin) == {"bytes", "sha256"}
            and type(pin["bytes"]) is int
            and 0 < pin["bytes"] <= MAX_WAV
            and type(pin["sha256"]) is str
            and re.fullmatch(r"[a-f0-9]{64}", pin["sha256"]),
            "Direct original WAV pin differs",
        )
    need(
        sum(pin["bytes"] for pin in pins.values()) <= MAX_TOTAL, "Direct WAV closure exceeds budget"
    )
    need(
        min(times) <= max(times) and len(organizations) == len(sensors) == 1,
        "Direct source owner or times differ",
    )
    return {
        "folder": folder,
        "pins": pins,
        "start_us": min(times),
        "end_us": max(times) + 1,
        "organization": organizations.pop(),
        "sensor": sensors.pop(),
    }


def _snapshot(head):
    return {key: head.get(key) for key in ("VersionId", "ETag", "ContentLength", "LastModified")}


def _fetch_wav(client, bucket, source_key, pin, target, *, checkpoint):
    """One source GET with before/after native HEAD and exact byte SHA."""
    checkpoint()
    require_bound_source_lease()
    before = client.head_object(Bucket=bucket, Key=source_key)
    need(before.get("ContentLength") == pin["bytes"], "Direct source WAV size changed")
    args = {"Bucket": bucket, "Key": source_key}
    if before.get("VersionId") is not None:
        args["VersionId"] = before["VersionId"]
    response = client.get_object(**args)
    body = response["Body"]
    digest = hashlib.sha256()
    total = 0
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            while block := body.read(65536):
                checkpoint()
                total += len(block)
                need(total <= pin["bytes"], "Direct source WAV exceeds bound")
                stream.write(block)
                digest.update(block)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        body.close()
    after = client.head_object(Bucket=bucket, Key=source_key)
    need(
        _snapshot(before) == _snapshot(after)
        and total == pin["bytes"]
        and digest.hexdigest() == pin["sha256"],
        "Direct source WAV changed or digest differs",
    )
    checkpoint()
    require_bound_source_lease()


def _source_client():
    """Use only the existing production Direct source, with a GET-only call site."""
    import boto3
    from botocore.config import Config

    from app.raw_archive.runner import source_settings

    settings = source_settings("direct", "production")
    need(
        settings["endpoint"] == "http://127.0.0.1:9000"
        and settings["bucket"] == "greenmind-direct-production-hotspot",
        "Exact existing Direct object source required",
    )
    client = boto3.client(
        "s3",
        endpoint_url=settings["endpoint"],
        aws_access_key_id=settings["access"],
        aws_secret_access_key=settings["secret"],
        config=Config(
            connect_timeout=3, read_timeout=8, retries={"total_max_attempts": 1}, proxies={}
        ),
    )
    return client, settings["bucket"]


def _runtime_guard(folder, checkpoint):
    need(os.geteuid() == 0 and callable(checkpoint), "Root Direct controller required")
    need(
        all(
            os.environ.get(name, "false") == "false"
            for name in (
                "RAW_ARCHIVE_DELETE_ENABLED",
                "TIERED_ARCHIVE_DELETE_ENABLED",
                "RETENTION_ENABLED",
                "DIRECT_RETENTION_ENABLED",
                "VISUAL_PRUNE_ENABLED",
                "TIERED_ARCHIVE_SQL_PRUNE_ENABLED",
            )
        ),
        "Direct deletion must remain disabled",
    )
    bound_job_lock()
    require_bound_source_lease()
    membership = Path("/proc/self/cgroup").read_text().strip()
    need(
        membership.startswith("0::/")
        and ".." not in Path(membership[3:]).parts
        and re.fullmatch(r"[a-f0-9]{32}", os.environ.get("INVOCATION_ID", "")),
        "Bounded Direct worker generation required",
    )
    group = Path("/sys/fs/cgroup") / membership[4:]
    try:
        maximum = int((group / "memory.max").read_text().strip())
        high = int((group / "memory.high").read_text().strip())
        swap = int((group / "memory.swap.max").read_text().strip())
        quota, period = (int(x) for x in (group / "cpu.max").read_text().split())
        events = dict(line.split() for line in (group / "memory.events").read_text().splitlines())
        available = next(
            int(line.split()[1]) * 1024
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")
        )
    except (OSError, ValueError, StopIteration) as exc:
        raise ArchiveBlocked("Direct worker resource controls unavailable") from exc
    need(
        0 < maximum <= 192 * 1024**2
        and 0 < high <= 160 * 1024**2
        and swap == 0
        and 0 < quota * 4 <= period
        and all(events.get(k) == "0" for k in ("high", "max", "oom", "oom_kill"))
        and available >= 1024 * 1024**2
        and shutil.disk_usage(folder).free >= 2499805184 + 96 * 1024**2,
        "Direct worker resource reserve unavailable",
    )
    checkpoint()


def _verify_sealed(path, ref, directory, key, *, checkpoint):
    with tempfile.TemporaryDirectory(prefix="direct-aes-check-", dir=directory) as temporary:
        target = Path(temporary) / "plain"
        backup.decrypt(path, target, key, ref["plaintext_sha256"], checkpoint=checkpoint)


def stage(work_parent, key, *, checkpoint, progress=None):
    """Freeze five captured JSONLs and three fresh Source GETs as AES parts.

    Creates a new root-private handoff, never publishes or changes Source. A
    partial directory is not reused; only a completed stage.json is resumable.
    """
    need(type(key) is bytes and len(key) in (16, 24, 32), "Archive AES key required")
    need(progress is None or callable(progress), "Valid Direct progress observer required")
    jsonl_sealed = wav_gets = wav_sealed = 0

    def report(phase):
        if progress is not None:
            progress(phase, jsonl_sealed, wav_gets, wav_sealed)

    started = time.monotonic()
    original_checkpoint = checkpoint

    def checkpoint():
        need(time.monotonic() - started < 75, "Direct stage deadline expired")
        original_checkpoint()
        require_bound_source_lease()

    parent = private_dir(work_parent)
    report("capture_admission")
    _runtime_guard(parent, checkpoint)
    report("capture_validate")
    source = inspect_capture(checkpoint=checkpoint)
    target = parent / ("direct-original-" + os.urandom(16).hex())
    target.mkdir(mode=0o700, exist_ok=False)
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    objects = []
    report("stage_directory_created")
    try:
        for number, (name, (size, sha)) in enumerate(CAPTURE_FILES.items(), 1):
            report("seal_jsonl")
            _runtime_guard(parent, checkpoint)
            original = source["folder"] / name
            sealed = target / f"{number:04d}.bin"
            ref = backup.encrypt(original, sealed, key, checkpoint=checkpoint)
            need(
                ref["plaintext_sha256"] == sha and original.stat().st_size == size,
                "Sealed Direct capture differs",
            )
            _verify_sealed(sealed, ref, target, key, checkpoint=checkpoint)
            _read_exact(original, size, sha, checkpoint=checkpoint)
            objects.append({"role": name, **ref})
            jsonl_sealed = number
            report("seal_jsonl")
        client, bucket = _source_client()
        try:
            for position, (source_key, pin) in enumerate(sorted(source["pins"].items()), 1):
                report("source_wav_get")
                _runtime_guard(parent, checkpoint)
                with tempfile.TemporaryDirectory(
                    prefix="direct-original-get-", dir=target
                ) as temporary:
                    raw = Path(temporary) / "source.wav"
                    _fetch_wav(client, bucket, source_key, pin, raw, checkpoint=checkpoint)
                    wav_gets = position
                    report("source_wav_seal")
                    ref = backup.encrypt(
                        raw, target / f"{position + 5:04d}.bin", key, checkpoint=checkpoint
                    )
                need(ref["plaintext_sha256"] == pin["sha256"], "Sealed Direct WAV differs")
                _verify_sealed(
                    target / f"{position + 5:04d}.bin", ref, target, key, checkpoint=checkpoint
                )
                objects.append(
                    {
                        "role": "original-wav-" + str(position),
                        "source_key_sha256": hashlib.sha256(source_key.encode()).hexdigest(),
                        "source_bytes": pin["bytes"],
                        **ref,
                    }
                )
                wav_sealed = position
                report("source_wav_seal")
        finally:
            client.close()
        _runtime_guard(parent, checkpoint)
        report("write_index")
        start = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=source["start_us"])
        end = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=source["end_us"])
        body = {
            "source": "direct",
            "source_rows_sha256": SOURCE_ROWS_SHA,
            "revision_manifest_sha256": REVISION_SHA,
            "dependency_revisions_sha256": DEPENDENCY_SHA,
            "chunks": SOURCE_CHUNKS,
            "original_payload_bytes": SOURCE_ORIGINAL_BYTES,
            "detached_reference_candidates": True,
            "accepted_source_reference": False,
            "objects": objects,
        }
        row = {
            "identity": codec.digest(body),
            "organization": source["organization"],
            "sensor": source["sensor"],
            "time_us": source["end_us"],
            "body": body,
        }
        index_path = target / "0000.parquet"
        index_ref = dict(parquet.write(index_path, [row], checkpoint=checkpoint), format="parquet")
        candidate = Candidate(
            "production",
            "direct",
            "direct_original_context",
            source["organization"],
            start,
            end,
            index_ref["content_sha256"],
            1,
            "direct_payload",
            end,
            quarantined=True,
        )
        candidate.validate()
        result = {
            "candidate": pipeline.candidate_json(candidate),
            "files": [
                index_ref,
                *[
                    {
                        k: v
                        for k, v in obj.items()
                        if k not in {"role", "source_key_sha256", "source_bytes"}
                    }
                    for obj in objects
                ],
            ],
        }
        raw = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        report("write_stage_handoff")
        fd = os.open(
            target / "stage.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        report("stage_complete")
        return {
            "stage": str(target),
            "candidate": candidate.identity,
            "quarantined": True,
            "source_GETs": 3,
            "published": False,
        }
    except BaseException:
        # The private incomplete directory is deliberately not reused or removed.
        raise


def _check_staged_file(path, reference):
    info = path.lstat()
    need(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == 0
        and info.st_nlink == 1
        and stat.S_IMODE(info.st_mode) == 0o600
        and reference.get("bytes") == info.st_size
        and checksum(path) == reference.get("sha256"),
        "Direct sealed stage file changed",
    )


def publish_stage(directory, store, ledger, *, checkpoint):
    """Publish an exact already-sealed handoff through the existing pipeline."""
    from .ledger import Ledger, _private_read
    from .runtime import candidate_from_json
    from .storage import Store

    started = time.monotonic()
    original_checkpoint = checkpoint

    def checkpoint():
        need(time.monotonic() - started < 75, "Direct publication deadline expired")
        original_checkpoint()
        require_bound_source_lease()

    root = private_dir(directory)
    _runtime_guard(root, checkpoint)
    need(
        type(store) is Store and type(ledger) is Ledger and not ledger.read_only,
        "Actual writable Direct archive adapters required",
    )
    raw = _private_read(root / "stage.json", 65536)
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ArchiveBlocked("Direct sealed stage is invalid") from exc
    need(
        type(value) is dict
        and set(value) == {"candidate", "files"}
        and type(value["files"]) is list
        and len(value["files"]) == 9,
        "Complete Direct sealed stage required",
    )
    candidate = candidate_from_json(value["candidate"])
    candidate.validate()
    need(
        candidate.environment == "production"
        and candidate.source == "direct"
        and candidate.dataset == "direct_original_context"
        and candidate.kind == "direct_payload"
        and candidate.quarantined is True
        and not any(
            (
                candidate.complete,
                candidate.features_verified,
                candidate.projections_verified,
                candidate.dependencies_complete,
            )
        ),
        "Direct quarantine binding differs",
    )
    files = []
    for number, ref in enumerate(value["files"]):
        _runtime_guard(root, checkpoint)
        path = root / ("0000.parquet" if number == 0 else f"{number:04d}.bin")
        need(
            type(ref) is dict
            and (number == 0 or set(ref) == {"format", "sha256", "bytes", "plaintext_sha256"}),
            "Direct sealed reference shape differs",
        )
        _check_staged_file(path, ref)
        files.append((path, ref))
    need(
        [ref.get("format") for _, ref in files] == ["parquet"] + ["bin"] * 8,
        "Direct sealed artifact order differs",
    )
    rows = list(parquet.read(files[0][0], files[0][1], checkpoint=checkpoint))
    need(
        len(rows) == 1
        and rows[0]["identity"] == codec.digest(rows[0]["body"])
        and rows[0]["organization"] == candidate.organization
        and files[0][1]["content_sha256"] == candidate.content_sha256,
        "Direct sealed index differs",
    )
    body = rows[0]["body"]
    need(
        type(body) is dict
        and body.get("source_rows_sha256") == SOURCE_ROWS_SHA
        and body.get("revision_manifest_sha256") == REVISION_SHA
        and body.get("dependency_revisions_sha256") == DEPENDENCY_SHA
        and body.get("chunks") == SOURCE_CHUNKS
        and body.get("original_payload_bytes") == SOURCE_ORIGINAL_BYTES
        and body.get("detached_reference_candidates") is True
        and body.get("accepted_source_reference") is False,
        "Direct source capture index differs",
    )
    objects = body.get("objects")
    need(
        type(objects) is list
        and len(objects) == 8
        and [obj.get("role") for obj in objects]
        == [*CAPTURE_FILES, "original-wav-1", "original-wav-2", "original-wav-3"]
        and all(
            {k: v for k, v in obj.items() if k not in {"role", "source_key_sha256", "source_bytes"}}
            == files[i + 1][1]
            for i, obj in enumerate(objects)
        ),
        "Direct encrypted artifact references differ",
    )
    need(
        all(
            objects[i]["plaintext_sha256"] == expected[1]
            for i, expected in enumerate(CAPTURE_FILES.values())
        ),
        "Direct five original capture digests differ",
    )
    source = inspect_capture(checkpoint=checkpoint)
    expected_wavs = [
        (hashlib.sha256(name.encode()).hexdigest(), pin["sha256"], pin["bytes"])
        for name, pin in sorted(source["pins"].items())
    ]
    actual_wavs = [
        (item.get("source_key_sha256"), item.get("plaintext_sha256"), item.get("source_bytes"))
        for item in objects[5:]
    ]
    expected_start = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=source["start_us"])
    expected_end = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=source["end_us"])
    need(
        actual_wavs == expected_wavs
        and candidate.organization == source["organization"]
        and candidate.start == expected_start
        and candidate.end == candidate.settled_at == expected_end
        and rows[0]["sensor"] == source["sensor"]
        and rows[0]["time_us"] == source["end_us"],
        "Direct WAV source closure differs",
    )
    result = pipeline.publish(candidate, files, store, ledger, checkpoint=checkpoint)
    need(
        result.get("state") == "published" and result.get("source", {}).get("quarantined") is True,
        "Direct publication quarantine differs",
    )
    return {
        "state": "published",
        "candidate": candidate.identity,
        "quarantined": True,
        "archive_losslessness_proven": False,
    }


def _verify_archived_closure(rows, manifest, pins, candidates, revisions, wav_paths, *, checkpoint):
    """Reconstruct detached candidates exclusively from independently read archive WAVs."""
    from types import SimpleNamespace

    from app.direct.payloads import WavReader, reconstruct

    from .recovery import archived_direct_original

    need(
        len(rows) == len(candidates) == SOURCE_CHUNKS
        and type(pins) is dict
        and len(pins) == len(wav_paths) == 3
        and set(pins) == set(wav_paths),
        "Archived Direct closure count differs",
    )
    need(
        type(manifest) is dict
        and hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == REVISION_SHA
        and codec.digest(revisions) == DEPENDENCY_SHA,
        "Archived Direct revision evidence differs",
    )
    candidate_map = {}
    for item in candidates:
        need(
            type(item) is dict
            and set(item)
            == {
                "kind",
                "chunk_id",
                "persisted_reference_present",
                "accepted_reference_proven",
                "original_metadata_sha256",
                "original_payload_sha256",
                "original_payload_bytes",
                "candidate",
            }
            and item["kind"] == "detached-original-piece-published-revision-candidate-v1"
            and item["persisted_reference_present"] is False
            and item["accepted_reference_proven"] is False
            and item["chunk_id"] not in candidate_map,
            "Archived detached candidate identity differs",
        )
        candidate_map[item["chunk_id"]] = item
    revision_map = {}
    for item in revisions:
        need(
            type(item) is dict
            and set(item) == {"reference", "record"}
            and type(item["reference"]) is list
            and len(item["reference"]) == 2,
            "Archived Direct revision record differs",
        )
        pair = tuple(item["reference"])
        record = item["record"]
        need(
            pair not in revision_map
            and type(record) is dict
            and type(record.get("manifest")) is dict
            and record.get("verified_at") is not None
            and record.get("raw_deleted_at") is None
            and record.get("manifest_sha256")
            == hashlib.sha256(
                json.dumps(record["manifest"], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            and record["manifest"].get("revision") == pair[1],
            "Archived Direct published revision differs",
        )
        revision_map[pair] = record
    used = set()

    def archive_get(key):
        checkpoint()
        need(key in wav_paths and key in pins, "Direct reconstruction archive WAV absent")
        path = wav_paths[key]
        need(
            path.stat().st_size == pins[key]["bytes"] and checksum(path) == pins[key]["sha256"],
            "Direct restored WAV differs",
        )
        used.add(key)
        return path.read_bytes()

    reader = WavReader(archive_get, cache_bytes=1024**2)
    digest = hashlib.sha256()
    total = 0
    seen = set()
    for envelope in rows:
        checkpoint()
        need(
            type(envelope) is dict and type(envelope.get("body")) is dict,
            "Archived Direct row envelope differs",
        )
        row, context = envelope["body"]["row"], envelope["body"]["context"]
        need(
            type(row) is dict
            and type(context) is dict
            and row.get("payload") is None
            and context.get("payload_reference") is None,
            "Original NULL Source references were not preserved",
        )
        chunk_id = row["id"]
        need(
            chunk_id not in seen and chunk_id in candidate_map,
            "Archived Direct candidate coverage differs",
        )
        seen.add(chunk_id)
        candidate = candidate_map[chunk_id]
        original_context = {k: v for k, v in context.items() if k != "original_payload"}
        need(
            candidate["original_metadata_sha256"]
            == codec.digest({"row": row, "context": original_context})
            and candidate["original_payload_sha256"] == row["payload_sha256"]
            and candidate["original_payload_bytes"] == row["payload_bytes"],
            "Archived detached candidate provenance differs",
        )
        payload = archived_direct_original(envelope["body"])
        total += len(payload)
        proposed = candidate["candidate"]
        need(
            type(proposed) is dict
            and set(proposed) == {"version", "parts"}
            and proposed["version"] == 1
            and type(proposed["parts"]) is list
            and 1 <= len(proposed["parts"]) <= 16,
            "Archived detached reference differs",
        )
        pieces = sorted(context["pieces"], key=lambda item: item["payload_offset"])
        parts = sorted(proposed["parts"], key=lambda item: item["chunk_offset"])
        need(len(parts) == len(pieces), "Archived detached piece count differs")
        for part, piece in zip(parts, pieces, strict=True):
            need(
                (part.get("segment_id"), part.get("chunk_offset"), part.get("frame_count"))
                == (piece["segment_id"], piece["payload_offset"], piece["frame_count"]),
                "Archived detached piece binding differs",
            )
            pair = (part["segment_id"], part["revision"])
            need(pair in revision_map, "Archived neighboring revision absent")
            record = revision_map[pair]
            revision = record["manifest"]
            need(
                (record.get("device_id"), record.get("session_id"), record.get("manifest_sha256"))
                == (row["device_id"], row["session_id"], part["manifest_sha256"])
                and (revision.get("device_id"), revision.get("session_id"), revision.get("config"))
                == (row["device_id"], row["session_id"], context["session_config"]),
                "Archived neighboring revision identity differs",
            )
            matches = [
                run
                for run in revision["runs"]
                if (
                    run.get("key"),
                    run.get("sha256"),
                    run.get("first_frame"),
                    run.get("frame_count"),
                )
                == (part["key"], part["sha256"], part["first_frame"], part["run_frames"])
            ]
            need(
                len(matches) == 1
                and part["key"] in pins
                and part["sha256"] == pins[part["key"]]["sha256"],
                "Archived WAV and revision binding differs",
            )
        try:
            restored = reconstruct(
                SimpleNamespace(**row),
                SimpleNamespace(**proposed),
                context["session_config"],
                reader,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ArchiveBlocked("Archived detached WAV reconstruction failed") from exc
        need(restored == payload, "Archived reconstructed original differs")
        raw = codec.encode(envelope)
        digest.update(len(raw).to_bytes(8, "big") + raw)
    need(
        len(seen) == len(candidate_map) == SOURCE_CHUNKS
        and total == SOURCE_ORIGINAL_BYTES
        and digest.hexdigest() == SOURCE_ROWS_SHA
        and used == set(pins),
        "Archived Direct original closure differs",
    )
    return {
        "chunks": len(seen),
        "original_bytes": total,
        "source_rows_sha256": digest.hexdigest(),
        "independently_restored_wavs": len(used),
    }


def independent_readback(
    identity,
    ledger,
    publisher,
    verifier,
    key,
    *,
    provider_proof_path,
    provider_proof_sha256,
    provider_terminal_path,
    provider_terminal_sha256,
    checkpoint,
):
    """Read every artifact through the separately provider-proved RO identity.

    Does not contact Source, update a receipt, mark a snapshot, or lift quarantine.
    """
    from .ledger import Ledger
    from .policy import POLICY_SHA256
    from .runtime import candidate_from_json
    from .scientific_registry_import import _provider_proof, _transport

    need(
        type(identity) is str
        and re.fullmatch(r"[a-f0-9]{64}", identity)
        and type(ledger) is Ledger
        and type(key) is bytes
        and len(key) in (16, 24, 32),
        "Exact Direct readback identity required",
    )
    started = time.monotonic()
    original_checkpoint = checkpoint

    def checkpoint():
        need(time.monotonic() - started < 75, "Direct independent readback deadline expired")
        original_checkpoint()
        require_bound_source_lease()

    _runtime_guard(ledger.root, checkpoint)
    _transport(publisher, verifier)
    _provider_proof(
        provider_proof_path, provider_proof_sha256, provider_terminal_path, provider_terminal_sha256
    )
    receipt = ledger.get(identity)
    need(
        type(receipt) is dict
        and receipt.get("state") in {"published", "verified"}
        and receipt.get("destination") == verifier.identity,
        "Actual Direct archive receipt required",
    )
    candidate = candidate_from_json(receipt["source"])
    candidate.validate()
    need(
        candidate.identity == identity
        and candidate.environment == "production"
        and candidate.source == "direct"
        and candidate.dataset == "direct_original_context"
        and candidate.kind == "direct_payload"
        and candidate.quarantined is True,
        "Direct readback quarantine differs",
    )
    refs = receipt.get("files")
    manifest_ref = receipt.get("manifest")
    need(
        type(refs) is list
        and len(refs) == 9
        and all(type(ref) is dict for ref in refs)
        and type(manifest_ref) is dict
        and type(manifest_ref.get("bytes")) is int
        and 0 < manifest_ref["bytes"] <= 1024**2,
        "Bounded Direct archive inventory required",
    )
    with tempfile.TemporaryDirectory(
        prefix="direct-independent-readback-", dir=ledger.root
    ) as temporary:
        root = Path(temporary)
        manifest_path = root / "manifest.json"
        checkpoint()
        verifier.download(manifest_ref["key"], manifest_path, manifest_ref["bytes"])
        need(
            manifest_path.stat().st_size == manifest_ref["bytes"]
            and checksum(manifest_path) == manifest_ref["sha256"],
            "Independent Direct manifest differs",
        )
        try:
            manifest = json.loads(manifest_path.read_bytes())
        except (ValueError, UnicodeError) as exc:
            raise ArchiveBlocked("Independent Direct manifest encoding differs") from exc
        need(
            manifest
            == {
                "version": 1,
                "policy": POLICY_SHA256,
                "candidate": pipeline.candidate_json(candidate),
                "identity": identity,
                "files": refs,
            },
            "Independent Direct manifest binding differs",
        )
        index = root / "index.parquet"
        verifier.download(refs[0]["key"], index, refs[0]["bytes"])
        envelopes = list(parquet.read(index, refs[0], checkpoint=checkpoint))
        need(
            len(envelopes) == 1
            and envelopes[0]["identity"] == codec.digest(envelopes[0]["body"])
            and refs[0].get("content_sha256") == candidate.content_sha256
            and envelopes[0]["organization"] == candidate.organization,
            "Independent Direct index differs",
        )
        body = envelopes[0]["body"]
        need(
            type(body) is dict
            and body.get("source") == "direct"
            and body.get("source_rows_sha256") == SOURCE_ROWS_SHA
            and body.get("revision_manifest_sha256") == REVISION_SHA
            and body.get("dependency_revisions_sha256") == DEPENDENCY_SHA
            and body.get("chunks") == SOURCE_CHUNKS
            and body.get("original_payload_bytes") == SOURCE_ORIGINAL_BYTES
            and body.get("detached_reference_candidates") is True
            and body.get("accepted_source_reference") is False,
            "Independent Direct capture descriptor differs",
        )
        objects = body.get("objects")
        need(
            type(objects) is list
            and len(objects) == 8
            and [x.get("role") for x in objects]
            == [*CAPTURE_FILES, "original-wav-1", "original-wav-2", "original-wav-3"],
            "Independent Direct artifact order differs",
        )
        restored = []
        for number, ref in enumerate(refs[1:], 1):
            checkpoint()
            need(
                ref.get("format") == "bin"
                and type(ref.get("bytes")) is int
                and 0 < ref["bytes"] <= MAX_WAV + 256
                and {
                    k: v
                    for k, v in objects[number - 1].items()
                    if k not in {"role", "source_key_sha256", "source_bytes"}
                }
                == {k: v for k, v in ref.items() if k != "key"},
                "Independent Direct encrypted reference differs",
            )
            sealed, plain = root / f"{number:04d}.bin", root / f"{number:04d}.plain"
            verifier.download(ref["key"], sealed, ref["bytes"])
            need(
                sealed.stat().st_size == ref["bytes"] and checksum(sealed) == ref["sha256"],
                "Independent Direct ciphertext differs",
            )
            backup.decrypt(sealed, plain, key, ref["plaintext_sha256"], checkpoint=checkpoint)
            restored.append(plain)
        for number, (_, (size, sha)) in enumerate(CAPTURE_FILES.items()):
            need(
                restored[number].stat().st_size == size
                and checksum(restored[number]) == sha
                and refs[number + 1]["plaintext_sha256"] == sha,
                "Independent Direct captured JSONL differs",
            )
        rows = _lines(restored[0].read_bytes(), SOURCE_CHUNKS)
        manifests = _lines(restored[1].read_bytes(), 1)
        pins_list = _lines(restored[2].read_bytes(), 1)
        candidates = _lines(restored[3].read_bytes(), SOURCE_CHUNKS)
        revisions = _lines(restored[4].read_bytes(), 16)
        need(
            len(manifests) == len(pins_list) == 1
            and type(pins_list[0]) is dict
            and len(pins_list[0]) == 3,
            "Independent Direct manifest or WAV pins differ",
        )
        pins = pins_list[0]
        wav_paths = {}
        for position, (source_key, pin) in enumerate(sorted(pins.items())):
            checkpoint()
            need(
                type(source_key) is str and type(pin) is dict and set(pin) == {"bytes", "sha256"},
                "Independent Direct WAV pin malformed",
            )
            item = objects[position + 5]
            need(
                item.get("source_key_sha256") == hashlib.sha256(source_key.encode()).hexdigest()
                and item.get("source_bytes") == pin["bytes"]
                and item.get("plaintext_sha256") == pin["sha256"]
                and restored[position + 5].stat().st_size == pin["bytes"]
                and checksum(restored[position + 5]) == pin["sha256"],
                "Independent Direct WAV binding differs",
            )
            wav_paths[source_key] = restored[position + 5]
        proof = _verify_archived_closure(
            rows, manifests[0], pins, candidates, revisions, wav_paths, checkpoint=checkpoint
        )
        checkpoint()
        need(ledger.get(identity) == receipt, "Direct archive receipt changed during readback")
        return {
            "status": "independent_readback",
            "candidate": identity,
            **proof,
            "source_data_accesses": 0,
            "quarantined": True,
            "snapshot_accepted": False,
            "archive_losslessness_proven": False,
        }
