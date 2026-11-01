"""Actual Arrow readback keeps all validations while avoiding its decoder pool."""

import pyarrow.parquet as pq
import pytest

from app.raw_archive.policy import ArchiveBlocked
from app.tiered_archive import parquet


def test_full_typed_roundtrip_explicitly_disables_parallel_decode(tmp_path, monkeypatch):
    rows = [
        dict(
            identity=f"row-{n:04}",
            organization="org",
            sensor="sensor",
            time_us=n,
            body={"row": {"binary": b"\x00\xff", "value": n}, "context": {}},
        )
        for n in range(513)
    ]
    path = tmp_path / "rows.parquet"
    proof = parquet.write(path, rows)
    actual = pq.ParquetFile
    calls = []

    class CheckedFile:
        def __init__(self, *args, **kwargs):
            self.source = actual(*args, **kwargs)

        def __getattr__(self, key):
            return getattr(self.source, key)

        def iter_batches(self, **kwargs):
            calls.append(kwargs)
            assert kwargs["use_threads"] is False and kwargs["batch_size"] == 128
            return self.source.iter_batches(**kwargs)

    monkeypatch.setattr(pq, "ParquetFile", CheckedFile)
    assert list(parquet.read(path, proof)) == rows
    parquet.verify(path, proof)
    assert len(calls) == 2
    with pytest.raises(ArchiveBlocked, match="logical contents"):
        parquet.verify(path, dict(proof, content_sha256="0" * 64))
    assert len(calls) == 3


def test_serial_decode_keeps_file_checksum_rejection(tmp_path, monkeypatch):
    path = tmp_path / "rows.parquet"
    proof = parquet.write(
        path, [dict(identity="one", organization="org", sensor="s", time_us=1, body={})]
    )
    path.write_bytes(path.read_bytes() + b"corruption")
    monkeypatch.setattr(
        pq, "ParquetFile", lambda *a, **k: pytest.fail("corruption must fail before decode")
    )
    with pytest.raises(ArchiveBlocked, match="checksum"):
        parquet.verify(path, proof)
