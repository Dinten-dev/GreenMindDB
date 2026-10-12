"""Capture framing cannot conceal missing rows, NULLs or unsafe payloads."""

import io
import json
import os
import struct
from uuid import UUID

import pytest

from app.raw_archive import catalog_capture as capture
from app.raw_archive.policy import ArchiveBlocked


def framing(values):
    out = capture.SIGNATURE + struct.pack("!ii", 0, 0)
    for row in values:
        out += struct.pack("!h", len(row))
        for value in row:
            out += struct.pack("!i", -1 if value is None else len(value))
            if value is not None:
                out += value
    return out + struct.pack("!h", -1)


def test_null_empty_literal_and_embedded_newlines_survive(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(framing([[None, b"", b"\\N", b'quote,"\ntext', "Gemüse".encode()]]))
    assert list(capture.rows(path, ["text"] * 5)) == [[None, "", "\\N", 'quote,"\ntext', "Gemüse"]]


def test_precise_types_and_original_bytea(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(
        framing(
            [
                [
                    b"9223372036854775807",
                    b"0.12345678901234568",
                    b"true",
                    b'{"x":null}',
                    b"\\x00ff",
                    b"2026-10-10 11:30:00.123456+00",
                    b"1.234567890123456789",
                ]
            ]
        )
    )
    assert list(
        capture.rows(path, ["int8", "float8", "bool", "jsonb", "bytea", "timestamptz", "numeric"])
    ) == [
        [
            9223372036854775807,
            0.12345678901234568,
            True,
            {"x": None},
            {"__bytea__": "AP8="},
            "2026-10-10 11:30:00.123456+00:00",
            "1.234567890123456789",
        ]
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: b"BAD" + data[3:],
        lambda data: data[:-1],
        lambda data: data + b"extra",
        lambda data: data[:11] + struct.pack("!ii", 1, 0) + data[19:],
        lambda data: data[:11] + struct.pack("!ii", 0, 1000000000) + data[19:],
        lambda data: data[:19] + struct.pack("!h", 2) + data[21:],
        lambda data: data[:21] + struct.pack("!i", -2) + data[25:],
        lambda data: data[:21] + struct.pack("!i", capture.wav_catalog.ROW_BYTES + 1) + data[25:],
    ],
)
def test_corrupt_framing_rejected(tmp_path, mutate):
    path = tmp_path / "input"
    path.write_bytes(mutate(framing([[b"original"]])))
    with pytest.raises(ArchiveBlocked):
        list(capture.rows(path, ["text"]))


def test_sink_checks_guard_before_writing():
    out = io.BytesIO()

    def blocked():
        raise ArchiveBlocked("reserve")

    sink = capture.Sink(out, blocked, 100)
    with pytest.raises(ArchiveBlocked, match="reserve"):
        sink.write(b"source")
    assert out.getvalue() == b""


def test_sink_rejects_growth_without_partial_extra_bytes():
    out = io.BytesIO()
    sink = capture.Sink(out, lambda: None, 5)
    sink.write(b"1234")
    with pytest.raises(ArchiveBlocked, match="disk"):
        sink.write(b"56")
    assert out.getvalue() == b"1234"


@pytest.mark.parametrize(
    "value,typ",
    [
        (b"t", "bool"),
        (b"2026-10-10 00:00:00", "timestamptz"),
        (b"2026-10-10 00:00:00+00", "timestamp"),
        (b"00", "bytea"),
        (b"x", "unknown"),
    ],
)
def test_ambiguous_type_rejected(value, typ):
    with pytest.raises(ArchiveBlocked):
        capture.decoded(value, typ)


def completed_fixture(tmp_path):
    source = tmp_path / "capture"
    source.mkdir(mode=0o700)
    entries = []
    for table, fields in capture.wav_catalog.COLUMNS["gateway"].items():
        values = [[b"a5d0e05b-e2b1-414a-a10a-e4e17ea88d57"]] if table == "organization" else []
        path = source / (table + ".pgcopy")
        path.write_bytes(framing(values))
        path.chmod(0o600)
        entries.append(
            {
                "table": table,
                "file": path.name,
                "types": ["text"] * len(fields),
                "rows": len(values),
                "bytes": path.stat().st_size,
                "sha256": capture.checksum(path),
            }
        )
    proof = {
        "schema": 1,
        "format": "wav_pgcopy_text_v1",
        "kind": "gateway",
        "allowlist_sha256": capture.wav_catalog.ALLOWLIST_SHA256,
        "started_at": "2026-10-10T10:00:00+00:00",
        "finished_at": "2026-10-10T10:00:02+00:00",
        "complete": True,
        "source_read_only": True,
        "files": entries,
    }
    capture.wav_catalog.save_json(source / "capture.json", proof)
    return source, proof


def test_offline_validation_preserves_source_window_and_empty_counts(tmp_path):
    source, proof = completed_fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    stage = capture.process(source, tmp_path / "processed")
    assert stage["started_at"] == proof["started_at"]
    assert stage["finished_at"] == proof["finished_at"]
    assert stage["tables"] == {
        table: int(table == "organization") for table in capture.wav_catalog.COLUMNS["gateway"]
    }
    assert before == {p.name: p.read_bytes() for p in source.iterdir()}


@pytest.mark.parametrize(
    "change", ["truncated_table", "wrong_rows", "missing_table", "long_source", "unknown_null_type"]
)
def test_incomplete_capture_never_publishes_stage(tmp_path, change):
    source, proof = completed_fixture(tmp_path)
    if change == "truncated_table":
        (source / proof["files"][0]["file"]).write_bytes(b"truncated")
    if change == "wrong_rows":
        proof["files"][0]["rows"] = 2
    if change == "missing_table":
        proof["files"].pop()
    if change == "long_source":
        proof["finished_at"] = "2026-10-10T10:02:01+00:00"
    if change == "unknown_null_type":
        proof["files"][-1]["types"][0] = "unknown"
    path = source / "capture.json"
    with path.open("w") as body:
        os.fchmod(body.fileno(), 0o600)
        json.dump(proof, body)
    target = tmp_path / "processed"
    with pytest.raises(ArchiveBlocked):
        capture.process(source, target)
    assert not (target / "stage.json").exists()


def test_source_guard_opens_no_database_or_output(tmp_path):
    class Engine:
        def connect(self):
            pytest.fail("No SQL during resource pause")

    def paused():
        raise ArchiveBlocked("memory")

    target = tmp_path / "new"
    with pytest.raises(ArchiveBlocked, match="memory"):
        capture.capture(Engine(), "gateway", target, paused)
    assert not target.exists()


def cursor_fixture(monkeypatch, pages):
    from types import SimpleNamespace

    class Cursor:
        closed = False
        queries = []

        def execute(self, query):
            self.queries.append(query)

        def fetchmany(self, size):
            assert size == 1000
            return pages.pop(0)

        def close(self):
            self.closed = True

    cursor = Cursor()
    raw = SimpleNamespace(cursor=lambda **kwargs: cursor)
    db = SimpleNamespace(connection=SimpleNamespace(driver_connection=raw))
    monkeypatch.setattr("psycopg2.extensions.register_type", lambda *args: None)
    monkeypatch.setattr(capture.time, "sleep", lambda _: None)
    return db, cursor


def test_sequential_cursor_preserves_raw_json_nulls_and_bool(tmp_path, monkeypatch):
    db, cursor = cursor_fixture(monkeypatch, [[['{"x":null}', None, "true"]], []])
    out = io.BytesIO()
    sink = capture.Sink(out, lambda: None, 4096)
    descriptors = [("j", "jsonb", "b", 3802), ("v", "text", "b", 25), ("b", "bool", "b", 16)]
    assert (
        capture.stream_gateway_table(
            db, "wav_feature", ["j", "v", "b"], descriptors, sink, lambda: None
        )
        == 1
    )
    path = tmp_path / "raw"
    path.write_bytes(out.getvalue())
    assert list(capture.rows(path, ["jsonb", "text", "bool"])) == [[{"x": None}, None, True]]
    assert cursor.closed and cursor.queries == [
        'SELECT "j","v","b"::text FROM "public"."wav_feature"'
    ]


def test_sequential_cursor_checks_guard_before_sql(monkeypatch):
    db, cursor = cursor_fixture(monkeypatch, [])

    def blocked():
        raise ArchiveBlocked("reserve")

    with pytest.raises(ArchiveBlocked, match="reserve"):
        capture.stream_gateway_table(
            db,
            "sensor",
            ["id"],
            [("id", "uuid", "b", 2950)],
            capture.Sink(io.BytesIO(), lambda: None, 4096),
            blocked,
        )
    assert cursor.closed and cursor.queries == []


@pytest.mark.parametrize("row", [[42], ["a", "b"]])
def test_sequential_cursor_rejects_decoded_values_and_wrong_width(monkeypatch, row):
    db, cursor = cursor_fixture(monkeypatch, [[row]])
    with pytest.raises(ArchiveBlocked):
        capture.stream_gateway_table(
            db,
            "sensor",
            ["id"],
            [("id", "uuid", "b", 2950)],
            capture.Sink(io.BytesIO(), lambda: None, 4096),
            lambda: None,
        )
    assert cursor.closed


def test_physical_ranges_preserve_one_snapshot_and_exact_edges(tmp_path, monkeypatch):
    from types import SimpleNamespace

    statements, bounds = [], []

    def execute(statement, parameters=None):
        statements.append(str(statement))
        return SimpleNamespace(one=lambda: (2400 * 8192, 8192, "r", False))

    class Cursor:
        rowcount = 1

        def mogrify(self, query, parameters):
            bounds.append(parameters)
            return query.encode()

        def copy_expert(self, query, chunk):
            chunk.write(framing([[str(len(bounds)).encode()]]))

    delays = []
    monkeypatch.setattr(capture.time, "sleep", delays.append)
    path = tmp_path / "capture"
    with path.open("wb") as out:
        assert (
            capture.copy_gateway_blocks(
                SimpleNamespace(execute=execute),
                Cursor(),
                "sensor",
                ["id"],
                capture.Sink(out, lambda: None, 4096),
                out,
                lambda: None,
            )
            == 3
        )
    assert bounds == [("(0,0)", "(1024,0)"), ("(1024,0)", "(2048,0)"), ("(2048,0)", "(2400,0)")]
    assert list(capture.rows(path, ["text"])) == [["1"], ["2"], ["3"]]
    assert "WHERE false" in statements[0]
    assert delays == [0.05, 0.05, 0.05]


@pytest.mark.parametrize(
    "kind,inherited,block_size", [("p", False, 8192), ("r", True, 8192), ("r", False, 4096)]
)
def test_physical_scan_rejects_nonplain_or_inherited_tables(kind, inherited, block_size):
    from types import SimpleNamespace

    db = SimpleNamespace(
        execute=lambda *a: SimpleNamespace(one=lambda: (8192, block_size, kind, inherited))
    )
    with pytest.raises(ArchiveBlocked, match="plain heap"):
        capture.copy_gateway_blocks(
            db,
            None,
            "sensor",
            ["id"],
            capture.Sink(io.BytesIO(), lambda: None, 4096),
            None,
            lambda: None,
        )


@pytest.mark.parametrize("block_size", [1, 2, 7, 19, 64])
def test_copy_chunks_preserve_rows_under_arbitrary_block_boundaries(tmp_path, block_size):
    output = io.BytesIO()
    sink = capture.Sink(output, lambda: None, 1024)
    sink.write(capture.SIGNATURE + struct.pack("!ii", 0, 0))
    for values in ([[b"first", None]], [], [[b"", b"\\N"]]):
        chunk = capture.CopyRowsSink(sink)
        data = framing(values)
        for offset in range(0, len(data), block_size):
            chunk.write(data[offset : offset + block_size])
        chunk.finish()
    sink.write(struct.pack("!h", -1))
    path = tmp_path / "combined"
    path.write_bytes(output.getvalue())
    assert list(capture.rows(path, ["text", "text"])) == [["first", None], ["", "\\N"]]


@pytest.mark.parametrize(
    "data", [b"", framing([])[:-1], framing([]) + b"extra", b"BAD" + framing([])[3:]]
)
def test_copy_chunk_corruption_cannot_be_published(data):
    chunk = capture.CopyRowsSink(capture.Sink(io.BytesIO(), lambda: None, 1024))
    with pytest.raises(ArchiveBlocked):
        chunk.write(data)
        chunk.finish()


@pytest.mark.parametrize(
    "value,typ,expected",
    [
        (struct.pack("!h", -32768), "int2", -32768),
        (struct.pack("!i", 2147483647), "int4", 2147483647),
        (struct.pack("!q", 9223372036854775807), "int8", 9223372036854775807),
        (struct.pack("!d", 0.12345678901234568), "float8", 0.12345678901234568),
        (
            UUID("a5d0e05b-e2b1-414a-a10a-e4e17ea88d57").bytes,
            "uuid",
            "a5d0e05b-e2b1-414a-a10a-e4e17ea88d57",
        ),
        (b"\x01", "bool", True),
        (b"\x00", "bool", False),
        (b'\x01{"a":null}', "jsonb", {"a": None}),
        (b"\x00\xff", "bytea", {"__bytea__": "AP8="}),
        (struct.pack("!q", -1), "timestamptz", "1999-12-31T23:59:59.999999+00:00"),
        (struct.pack("!q", 1), "timestamp", "2000-01-01T00:00:00.000001"),
        (struct.pack("!i", -1), "date", "1999-12-31"),
        (struct.pack("!hhHHHH", 2, 0, 0, 3, 1, 2340), "numeric", "1.234"),
        (struct.pack("!hhHHH", 1, -2, 0x4000, 9, 1234), "numeric", "-0.000012340"),
        (struct.pack("!hhHH", 0, 0, 0, 4), "numeric", "0.0000"),
    ],
)
def test_native_network_values_keep_exact_source_precision(value, typ, expected):
    assert capture.native_decoded(value, typ) == expected
    assert capture.native_decoded(None, typ) is None


@pytest.mark.parametrize(
    "value,typ",
    [
        (b"x", "int4"),
        (b"x", "uuid"),
        (b"false", "bool"),
        (b"\x02{}", "jsonb"),
        (b"{}", "jsonb"),
        (b"x", "timestamp"),
        (struct.pack("!hhHH", 0, 0, 0xC000, 0), "numeric"),
        (struct.pack("!hhHHH", 1, 0, 0, 0, 10000), "numeric"),
    ],
)
def test_invalid_native_wire_values_fail_closed(value, typ):
    with pytest.raises(ArchiveBlocked):
        capture.native_decoded(value, typ)
