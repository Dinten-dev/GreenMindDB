import ctypes
import random
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.raw_archive.scientific_capture import HEADER, TRAILER, FrameCounter
from app.raw_archive.scientific_native import _counter_class, counter_factory


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("Existing C compiler required")
    source = Path(__file__).parents[2] / "app/raw_archive/scientific_frame.c"
    output = tmp_path_factory.mktemp("science-native") / "validator.so"
    result = subprocess.run([compiler, "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", str(source), "-o", str(output)], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode()
    return _counter_class(ctypes.CDLL(str(output)))


def frame(rows, columns):
    return HEADER + b"".join(struct.pack("!h", columns) + b"".join(struct.pack("!i", -1 if item is None else len(item)) + (item or b"") for item in row) for row in rows) + TRAILER


def outcome(factory, data, columns, sizes):
    counter = factory(columns)
    position = 0
    try:
        for size in sizes:
            counter.feed(data[position:position + size])
            position += size
        if position < len(data):
            counter.feed(data[position:])
        return ("rows", counter.finish())
    except ArchiveBlocked:
        return ("blocked",)


@pytest.mark.parametrize("columns", [1, 10, 62])
def test_all_byte_boundaries_and_random_fragmentation(native, columns):
    randomizer = random.Random(columns)  # noqa: S311 - reproducible parser fixture
    rows = [[None if (i + j) % 7 == 0 else bytes(randomizer.randrange(256) for _ in range((i * j) % 53)) for j in range(columns)] for i in range(12)]
    data = frame(rows, columns)
    for split in range(len(HEADER) + 10):
        assert outcome(native, data, columns, [split, 1, 2, 3, 4, 5]) == outcome(FrameCounter, data, columns, [split, 1, 2, 3, 4, 5]) == ("rows", 12)
    assert outcome(native, data, columns, [1] * len(data)) == ("rows", 12)


def test_corruption_truncation_trailing_and_large_rows(native):
    data = frame([[b"", None, b"\x00\xff"], [b"abc", b"def", b"ghi"]], 3)
    samples = [data[:end] for end in range(len(data))]
    samples += [data + b"x", b"x" + data[1:], HEADER + struct.pack("!h", 2) + TRAILER]
    samples += [HEADER + struct.pack("!h", 3) + struct.pack("!i", length) for length in (-2, 2**20, 2**20 + 1, 2**31 - 1)]
    for invalid in samples:
        assert outcome(native, invalid, 3, [1] * len(invalid)) == outcome(FrameCounter, invalid, 3, [1] * len(invalid)) == ("blocked",)


def test_zero_rows_and_invalid_column_counts(native):
    assert outcome(native, HEADER + TRAILER, 62, [1] * 21) == ("rows", 0)
    for count in (True, False, 0, -1, 32768, "62"):
        with pytest.raises(ArchiveBlocked):
            native(count)


def test_untrusted_library_fails_before_loading(tmp_path):
    library = tmp_path / "validator.so"
    library.write_bytes(b"not-a-library")
    with pytest.raises(ArchiveBlocked):
        counter_factory(library, "0" * 64)
