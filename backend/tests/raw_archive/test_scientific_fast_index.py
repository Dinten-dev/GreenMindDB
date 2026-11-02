"""Native scan/reference equivalence; no network or production capture."""

import ctypes
import hashlib
import json
import os
import random
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from app.raw_archive import scientific_fast_index as fast
from app.raw_archive import scientific_native as native
from app.raw_archive import scientific_parts as parts
from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.scientific_capture import HEADER, TRAILER
from tests.raw_archive.test_scientific_v3 import manifest, row


@pytest.fixture(scope="module")
def scanner(tmp_path_factory):
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("C compiler required")
    source = Path(__file__).parents[2] / "app/raw_archive/scientific_frame.c"
    output = tmp_path_factory.mktemp("fast-native") / "scanner.so"
    result = subprocess.run(
        [
            compiler,
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-shared",
            "-fPIC",
            str(source),
            "-o",
            str(output),
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode()
    output.chmod(0o400)
    library = ctypes.CDLL(str(output))
    cls = native._scanner_class(library)
    cls.test_library = library
    cls.test_path = output
    return cls


@pytest.fixture
def simulated_root(monkeypatch):
    original_lstat, original_fstat = Path.lstat, os.fstat

    class RootStat:
        def __init__(self, info):
            self.info, self.st_uid = info, 0
            self.st_mode = (
                info.st_mode & ~0o022 if (info.st_mode & 0o170000) == 0o040000 else info.st_mode
            )

        def __getattr__(self, name):
            return getattr(self.info, name)

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(Path, "lstat", lambda path: RootStat(original_lstat(path)))
    monkeypatch.setattr(os, "fstat", lambda fd: RootStat(original_fstat(fd)))


def test_bounded_scanner_offsets_match_rows_across_fragmentation(scanner):
    records = [row(10, i.to_bytes(4, "big")) for i in range(73)]
    body = b"".join(records) + TRAILER
    expected, offset = [], 0
    for record in records:
        offset += len(record)
        expected.append(offset)
    for seed in range(5):
        rand = random.Random(seed)  # noqa: S311 -- deterministic test chunks
        parser = scanner(10)
        for byte in HEADER:
            parser.feed(bytes([byte]))
        found, position = [], 0
        while position < len(body):
            size = rand.randrange(1, 300)
            chunk = body[position : position + size]
            consumed, ends = parser.scan_rows(chunk, capacity=2)
            found.extend(position + end for end in ends)
            position += consumed
        assert found == expected
        assert parser.finish() == len(records)


@pytest.mark.parametrize("source", ["gateway", "direct"])
def test_parent_mode_reuses_pinned_scanner_and_matches_reference_index(
    tmp_path, scanner, simulated_root, monkeypatch, source
):
    from app.raw_archive import parent_capture
    from tests.raw_archive.test_parent_capture import fixture_capture

    folder, proof, sha = fixture_capture(tmp_path, source)
    reference = parent_capture.prepare_index(
        folder, sha, tmp_path / "reference.json", checkpoint=lambda: None
    )
    monkeypatch.setattr(fast, "scanner_factory", lambda *a: scanner)
    result = fast.fast_prepare_index(
        folder,
        sha,
        tmp_path / "native.json",
        checkpoint=lambda: None,
        library=scanner.test_path,
        library_sha256=hashlib.sha256(scanner.test_path.read_bytes()).hexdigest(),
        target_part_bytes=parent_capture.MAX_PART_BYTES,
        parent_mode=True,
    )
    assert result["index"] == reference["index"]
    parent_capture.validate_index(proof, result["index"], sha)


def test_scanner_blocks_bad_lengths_trailers_and_output_bounds(scanner):
    for bad in (
        HEADER + row(10)[:-1],
        HEADER + row(10) + TRAILER + b"extra",
        HEADER + struct.pack("!h", 10) + struct.pack("!i", -2),
        HEADER + struct.pack("!h", 10) + struct.pack("!i", 2**20),
    ):
        instance = scanner(10)
        with pytest.raises(ArchiveBlocked):
            instance.scan_rows(bad)
            instance.finish()
    instance = scanner(1)
    for capacity in (0, -1, 20001, True):
        with pytest.raises(ArchiveBlocked):
            instance.scan_rows(b"x", capacity=capacity)
    with pytest.raises(ArchiveBlocked):
        instance.scan_rows(b"x" * (2 * 1024**2 + 1))


def test_fast_index_matches_reference_empty_rowlimit_and_cross_block(
    tmp_path, scanner, monkeypatch, simulated_root
):
    monkeypatch.setattr(fast, "scanner_factory", lambda *_: scanner)
    real_load = fast.load_capture
    loaded = []

    def load_once(*args, **kwargs):
        loaded.append(kwargs.get("verify_files"))
        return real_load(*args, **kwargs)

    monkeypatch.setattr(fast, "load_capture", load_once)
    records = [row(62, index.to_bytes(4, "big")) for index in range(20003)]
    source, _proof, capture_sha = manifest(tmp_path, {"wav_feature": records})
    reference = tmp_path / "reference.json"
    output = tmp_path / "native.json"
    expected = parts.prepare_index(source, capture_sha, reference, checkpoint=lambda: None)
    actual = fast.fast_prepare_index(
        source,
        capture_sha,
        output,
        checkpoint=lambda: None,
        library="/unused",
        library_sha256="0" * 64,
    )
    assert output.read_bytes() == reference.read_bytes()
    assert actual == expected
    assert loaded == [False]
    assert [part["rows"] for part in actual["index"]["parts"]] == [20000, 3, 0]


def test_fast_index_exact_byte_budget_boundary(tmp_path, scanner, monkeypatch, simulated_root):
    monkeypatch.setattr(fast, "scanner_factory", lambda *_: scanner)
    monkeypatch.setattr(fast, "MAX_PART_BYTES", 1024)
    monkeypatch.setattr(parts, "MAX_PART_BYTES", 1024)
    records = [row(10, bytes([i]) * 300) for i in range(8)]
    source, _proof, capture_sha = manifest(tmp_path, {"wav_feature_version": records})
    expected = parts.prepare_index(
        source, capture_sha, tmp_path / "reference.json", checkpoint=lambda: None
    )
    actual = fast.fast_prepare_index(
        source,
        capture_sha,
        tmp_path / "native.json",
        checkpoint=lambda: None,
        library="/unused",
        library_sha256="0" * 64,
        target_part_bytes=1024,
    )
    assert actual == expected
    assert [item["rows"] for item in actual["index"]["parts"]] == [0, 2, 2, 2, 2]
    with pytest.raises(ArchiveBlocked, match="Single V3 row exceeds part budget"):
        fast.fast_prepare_index(
            source,
            capture_sha,
            tmp_path / "too-small.json",
            checkpoint=lambda: None,
            library="/unused",
            library_sha256="0" * 64,
            target_part_bytes=300,
        )
    assert not (tmp_path / "too-small.json").exists()


def test_real_32mib_boundary_matches_reference(tmp_path, scanner, monkeypatch, simulated_root):
    monkeypatch.setattr(fast, "scanner_factory", lambda *_: scanner)
    record = row(10, b"x" * 999000)
    source, proof, capture_sha = manifest(tmp_path, {"wav_feature_version": [record] * 34})
    expected = parts.prepare_index(
        source, capture_sha, tmp_path / "reference.json", checkpoint=lambda: None
    )
    actual = fast.fast_prepare_index(
        source,
        capture_sha,
        tmp_path / "native.json",
        checkpoint=lambda: None,
        library="/unused",
        library_sha256="0" * 64,
    )
    assert actual == expected
    assert [item["rows"] for item in actual["index"]["parts"]] == [0, 33, 1]
    assert all(item["bytes"] <= 32 * 1024**2 for item in actual["index"]["parts"])
    smaller = fast.fast_prepare_index(
        source,
        capture_sha,
        tmp_path / "native-16mib.json",
        checkpoint=lambda: None,
        library="/unused",
        library_sha256="0" * 64,
        target_part_bytes=16 * 1024**2,
    )
    assert [item["rows"] for item in smaller["index"]["parts"]] == [0, 16, 16, 2]
    assert smaller["index"]["max_part_bytes"] == 32 * 1024**2
    assert all(item["bytes"] <= 16 * 1024**2 for item in smaller["index"]["parts"])
    parts.validate_index(proof, smaller["index"], capture_sha)
    with monkeypatch.context() as patch:
        patch.setattr(parts, "MAX_PART_BYTES", 16 * 1024**2)
        reference = parts.prepare_index(
            source, capture_sha, tmp_path / "reference-16mib.json", checkpoint=lambda: None
        )
    reference["index"]["max_part_bytes"] = 32 * 1024**2
    assert smaller["index"] == reference["index"]


def test_linux_releases_consumed_read_pages_at_eight_mib(
    tmp_path, scanner, monkeypatch, simulated_root
):
    monkeypatch.setattr(fast, "scanner_factory", lambda *_: scanner)
    record = row(10, b"x" * 999000)
    source, proof, capture_sha = manifest(tmp_path, {"wav_feature_version": [record] * 10})
    calls = []
    with monkeypatch.context() as patch:
        patch.setattr(fast.sys, "platform", "linux")
        patch.setattr(fast.os, "POSIX_FADV_DONTNEED", 4, raising=False)
        patch.setattr(
            fast.os,
            "posix_fadvise",
            lambda fd, start, length, advice: calls.append((start, length, advice)),
            raising=False,
        )
        fast.fast_prepare_index(
            source,
            capture_sha,
            tmp_path / "native.json",
            checkpoint=lambda: None,
            library="/unused",
            library_sha256="0" * 64,
        )
    # The empty table also receives its own final advisory call.
    nonempty = [call for call in calls if call[1] > len(HEADER) + len(TRAILER)]
    assert nonempty[0] == (0, 8 * 1024**2, 4)
    assert nonempty[1][0] == nonempty[0][1]
    assert sum(call[1] for call in nonempty) == proof["files"][1]["bytes"]
    assert all(call[1] <= 8 * 1024**2 for call in calls)


def test_linux_page_advice_failure_blocks_index(tmp_path, scanner, monkeypatch, simulated_root):
    monkeypatch.setattr(fast, "scanner_factory", lambda *_: scanner)
    source, _proof, capture_sha = manifest(tmp_path, {"wav_feature_version": [row(10)]})
    output = tmp_path / "native.json"

    def unavailable(*_args):
        raise OSError("advice failure")

    with monkeypatch.context() as patch:
        patch.setattr(fast.sys, "platform", "linux")
        patch.setattr(fast.os, "POSIX_FADV_DONTNEED", 4, raising=False)
        patch.setattr(fast.os, "posix_fadvise", unavailable, raising=False)
        with pytest.raises(ArchiveBlocked, match="page-cache release failed"):
            fast.fast_prepare_index(
                source,
                capture_sha,
                output,
                checkpoint=lambda: None,
                library="/unused",
                library_sha256="0" * 64,
            )
    assert not output.exists()


def test_loader_refuses_untrusted_or_digest_mismatched_library_before_dlopen(tmp_path, monkeypatch):
    path = tmp_path / "malicious.so"
    path.write_bytes(b"not-reviewed-native-code")
    called = []
    monkeypatch.setattr(native.ctypes, "CDLL", lambda *_: called.append(True))
    with pytest.raises(ArchiveBlocked):
        native.scanner_factory(path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert called == []


def test_root_pinned_loader_exposes_feed_and_scan(scanner, simulated_root):
    path = scanner.test_path
    factory = native.scanner_factory(path, hashlib.sha256(path.read_bytes()).hexdigest())
    parser = factory(1)
    parser.feed(HEADER)
    raw = struct.pack("!hi", 1, -1)
    consumed, offsets = parser.scan_rows(raw + TRAILER, capacity=1)
    assert (consumed, offsets) == (len(raw), [len(raw)])
    parser.feed(TRAILER)
    assert parser.finish() == 1


def test_fast_index_rejects_nonroot_before_loading_native_code(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 501)
    calls = []
    monkeypatch.setattr(fast, "scanner_factory", lambda *_: calls.append(True))
    with pytest.raises(ArchiveBlocked, match="Root-owned"):
        fast.fast_prepare_index(
            tmp_path,
            "a" * 64,
            tmp_path / "index.json",
            checkpoint=lambda: None,
            library="/untrusted",
            library_sha256="b" * 64,
        )
    assert calls == []


@pytest.mark.parametrize("target", [True, 0, 20, 32 * 1024**2 + 1, "16777216"])
def test_invalid_part_target_stops_before_loading(tmp_path, monkeypatch, simulated_root, target):
    calls = []
    monkeypatch.setattr(fast, "scanner_factory", lambda *_: calls.append(True))
    with pytest.raises(ArchiveBlocked, match="target part budget"):
        fast.fast_prepare_index(
            tmp_path,
            "a" * 64,
            tmp_path / "index.json",
            checkpoint=lambda: None,
            library="/untrusted",
            library_sha256="b" * 64,
            target_part_bytes=target,
        )
    assert calls == []


def test_root_mode_digest_mismatch_cannot_load_malicious_code(tmp_path, monkeypatch):
    path = tmp_path / "malicious.so"
    path.write_bytes(b"untrusted-constructor")
    path.chmod(0o400)
    original_lstat, original_fstat = Path.lstat, os.fstat

    class RootStat:
        def __init__(self, info):
            self.info = info
            self.st_uid = 0
            self.st_mode = (
                info.st_mode & ~0o022 if (info.st_mode & 0o170000) == 0o040000 else info.st_mode
            )

        def __getattr__(self, name):
            return getattr(self.info, name)

    monkeypatch.setattr(Path, "lstat", lambda path: RootStat(original_lstat(path)))
    monkeypatch.setattr(os, "fstat", lambda fd: RootStat(original_fstat(fd)))
    calls = []
    monkeypatch.setattr(native.ctypes, "CDLL", lambda *_: calls.append(True))
    with pytest.raises(ArchiveBlocked, match="digest differs"):
        native.scanner_factory(path, "0" * 64)
    assert calls == []


def test_native_api_rejects_excess_capacity_without_advancing(scanner):
    parser = scanner(1)
    parser.feed(HEADER)
    consumed, count = ctypes.c_size_t(999), ctypes.c_size_t(999)
    ends = (ctypes.c_uint32 * 1)()
    raw = struct.pack("!hi", 1, -1)
    result = scanner.test_library.gm_counter_scan_rows(
        parser.state, raw, len(raw), ctypes.byref(consumed), ends, 20001, ctypes.byref(count)
    )
    assert result != 0 and consumed.value == count.value == 999
    used, found = parser.scan_rows(raw + TRAILER, capacity=1)
    assert used == len(raw) and found == [len(raw)]
    parser.feed(TRAILER)
    assert parser.finish() == 1


def test_single_scan_rejects_tampered_source_without_writing_index(
    tmp_path, scanner, monkeypatch, simulated_root
):
    monkeypatch.setattr(fast, "scanner_factory", lambda *_: scanner)
    source, _proof, capture_sha = manifest(tmp_path, {"wav_feature": [row(62, b"abcd")]})
    path = source / "wav_feature.pgcopy"
    raw = bytearray(path.read_bytes())
    raw[len(HEADER) + 2 + 4] ^= 1
    path.chmod(0o600)
    path.write_bytes(raw)
    path.chmod(0o400)
    output = tmp_path / "bad.json"
    with pytest.raises(ArchiveBlocked, match="table hash differs"):
        fast.fast_prepare_index(
            source,
            capture_sha,
            output,
            checkpoint=lambda: None,
            library="/unused",
            library_sha256="0" * 64,
        )
    assert not output.exists()


def test_single_scan_rejects_wrong_captured_row_count(
    tmp_path, scanner, monkeypatch, simulated_root
):
    monkeypatch.setattr(fast, "scanner_factory", lambda *_: scanner)
    source, proof, _capture_sha = manifest(tmp_path, {"wav_feature": [row(62, b"abcd")]})
    proof["files"][0]["rows"] += 1
    raw = (json.dumps(proof, sort_keys=True, separators=(",", ":")) + "\n").encode()
    capture_path = source / "capture.json"
    capture_path.chmod(0o600)
    capture_path.write_bytes(raw)
    capture_path.chmod(0o400)
    output = tmp_path / "bad.json"
    with pytest.raises(ArchiveBlocked, match="row count differs"):
        fast.fast_prepare_index(
            source,
            hashlib.sha256(raw).hexdigest(),
            output,
            checkpoint=lambda: None,
            library="/unused",
            library_sha256="0" * 64,
        )
    assert not output.exists()


def test_counter_overflow_is_rejected_by_native(scanner):
    class State(ctypes.Structure):
        _fields_ = [
            ("rows", ctypes.c_uint64),
            ("columns", ctypes.c_uint32),
            ("fields", ctypes.c_uint32),
            ("remaining", ctypes.c_uint32),
            ("row_bytes", ctypes.c_uint32),
            ("stage", ctypes.c_uint),
            ("used", ctypes.c_uint),
            ("scratch", ctypes.c_ubyte * 19),
        ]

    parser = scanner(1)
    parser.feed(HEADER)
    state = State.from_buffer(parser.state)
    state.rows = 2**64 - 1
    with pytest.raises(ArchiveBlocked):
        parser.scan_rows(struct.pack("!hi", 1, -1))
