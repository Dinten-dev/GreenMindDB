"""Detached original reconstruction through the real archive and Parquet path."""

import copy
import hashlib
import io
import json
import wave
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, func, select

from app.direct.models import (
    Base,
    Chunk,
    Device,
    PayloadReference,
    Piece,
    Revision,
    Segment,
    Session,
)
from app.direct.protocol import ChunkMetadata


def fixture(bad_neighbor=None):
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def sqlite_size(connection, _):
        connection.create_function(
            "pg_column_size", 1, lambda value: None if value is None else len(value.encode())
        )

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def pg_cast_only(_, __, statement, parameters, ___, ____):
        return statement.replace("::bigint", ""), parameters

    Base.metadata.create_all(engine)
    connection = engine.connect()
    connection.begin()
    device, session = str(UUID(int=1)), str(UUID(int=2))
    connection.execute(
        Device.__table__.insert(),
        dict(
            id=device,
            organization_id=str(UUID(int=3)),
            zone_id=str(UUID(int=4)),
            mode="DIRECT",
            key_hash="a" * 64,
        ),
    )
    frames_per_bucket = 380 * 600
    rows = []
    for index, first in enumerate((frames_per_bucket - 1, 2 * frames_per_bucket - 1)):
        raw = bytes((index * 2 + 1, 0, index * 2 + 2, 0))
        meta = ChunkMetadata(
            protocol_version=1,
            device_id=device,
            session_id=session,
            sequence=index,
            session_start_us=1_789_999_800_000_000,
            first_frame=first,
            sample_rate=380,
            channels=1,
            sample_bits=16,
            frame_count=2,
            channel_labels=["ADC"],
            calibration_version="unsigned-mv-linear-int16-v1",
            firmware_version="synthetic",
            payload_sha256=hashlib.sha256(raw).hexdigest(),
        )
        row = dict(
            id=str(UUID(int=100 + index)),
            device_id=device,
            session_id=session,
            sequence=index,
            first_frame=first,
            frame_count=2,
            digest=meta.digest(),
            payload_sha256=meta.payload_sha256,
            payload=None,
            payload_bytes=4,
            received_at=1_790_000_000.0 + index,
        )
        connection.execute(Chunk.__table__.insert(), row)
        rows.append((row, raw))
    config = meta.session_config()
    connection.execute(
        Session.__table__.insert(), dict(device_id=device, id=session, config=config, mode="DIRECT")
    )
    waves = {}
    for index, (first, raw) in enumerate(
        (
            (frames_per_bucket - 1, b"\x01\x00"),
            (frames_per_bucket, b"\x02\x00" + b"\x00\x00" * (frames_per_bucket - 2) + b"\x03\x00"),
            (2 * frames_per_bucket, b"\x04\x00"),
        )
    ):
        segment = str(UUID(int=200 + index))
        connection.execute(
            Segment.__table__.insert(),
            dict(
                id=segment,
                device_id=device,
                session_id=session,
                bucket=2_983_333 + index,
                first_frame=first,
                end_frame=first + len(raw) // 2,
                revision=1,
                published_revision=1,
                sealed=True,
            ),
        )
        output = io.BytesIO()
        with wave.open(output, "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(380)
            stream.writeframes(raw)
        key = f"direct/{device}/{session}/{segment}.wav"
        waves[key] = output.getvalue()
        manifest = dict(
            device_id=device,
            session_id=session,
            revision=1,
            config=config,
            runs=[
                dict(
                    key=key,
                    sha256=hashlib.sha256(waves[key]).hexdigest(),
                    first_frame=first,
                    frame_count=len(raw) // 2,
                )
            ],
        )
        if not (index == 0 and bad_neighbor == "missing"):
            connection.execute(
                Revision.__table__.insert(),
                dict(
                    segment_id=segment,
                    revision=1,
                    manifest=manifest,
                    manifest_sha256="0" * 64
                    if index == 0 and bad_neighbor == "checksum"
                    else hashlib.sha256(
                        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest(),
                    verified_at=0.0
                    if index == 0 and bad_neighbor == "unverified"
                    else 1_790_000_000.0,
                ),
            )
        for chunk_index, offset in (
            ((0, 0),) if index == 0 else (((0, 1), (1, 0)) if index == 1 else ((1, 1),))
        ):
            connection.execute(
                Piece.__table__.insert(),
                dict(
                    segment_id=segment,
                    chunk_id=rows[chunk_index][0]["id"],
                    payload_offset=offset,
                    frame_count=1,
                ),
            )
    contexts = []
    for row, _ in rows:
        pieces = [
            dict(item)
            for item in connection.execute(
                select(Piece.__table__).where(Piece.chunk_id == row["id"])
            ).mappings()
        ]
        contexts.append(dict(session_config=config, pieces=pieces, payload_reference=None))
    statements = []

    @event.listens_for(connection, "before_cursor_execute")
    def readonly_only(_, __, statement, ___, ____, _____):
        statements.append(statement)
        assert statement.lstrip().upper().startswith("SELECT"), statement

    return engine, connection, rows, contexts, waves, statements


def test_two_boundary_chunks_three_wavs_roundtrip_from_archived_bytes(monkeypatch, tmp_path):
    from app.direct.payloads import WavReader
    from app.tiered_archive import parquet, runtime
    from app.tiered_archive.recovery import archived_direct_original, direct_children
    from app.tiered_archive.source import DATASETS, Source

    engine, db, rows, contexts, waves, statements = fixture()
    gets = []

    def get(key):
        gets.append(key)
        return waves[key]

    reader = WavReader(get, cache_bytes=1024**2)
    monkeypatch.setattr(runtime, "direct_wav_reader", lambda _namespace: reader)
    monkeypatch.setattr(runtime, "environment", lambda: "staging")
    adapter = Source(
        None,
        "direct",
        "staging",
        checkpoint=lambda: None,
        payload_reader=runtime.direct_payload_reader,
    )
    original_before = copy.deepcopy((rows, contexts))
    envelopes = []
    try:
        for index, ((row, expected), context) in enumerate(zip(rows, contexts, strict=True)):
            archived_context = adapter.context(db, DATASETS["direct_chunk"], row)
            assert archived_context["original_payload"] == expected
            assert archived_context["payload_reference"] is None and row["payload"] is None
            assert {piece["segment_id"] for piece in archived_context["pieces"]} == {
                piece["segment_id"] for piece in context["pieces"]
            }
            envelopes.append(
                dict(
                    identity=str(index),
                    organization=str(UUID(int=3)),
                    sensor=row["device_id"],
                    time_us=1_790_000_000_000_000 + index,
                    body={"row": row, "context": archived_context},
                )
            )
        assert original_before == (rows, contexts)
        assert db.execute(select(func.count()).select_from(PayloadReference)).scalar_one() == 0
        path = tmp_path / "originals.parquet"
        metadata = parquet.write(path, envelopes)
        restored = list(parquet.read(path, metadata))
        for envelope, (source_row, expected) in zip(restored, rows, strict=True):
            body = envelope["body"]
            assert body["row"] == source_row and body["row"]["payload"] is None
            assert body["context"]["payload_reference"] is None
            assert archived_direct_original(body) == expected
            assert (
                hashlib.sha256(archived_direct_original(body)).hexdigest()
                == source_row["payload_sha256"]
            )
            assert direct_children(body)["direct_chunk_payload_ref"] == []
        assert len(waves) == 3 and len(gets) == 3 and len(set(gets)) == 3
        assert statements and db.in_transaction()
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize("bad_neighbor", ["missing", "unverified", "checksum"])
def test_bad_neighbor_blocks_runtime_derived_original(monkeypatch, bad_neighbor):
    from app.direct.payloads import WavReader
    from app.raw_archive.policy import ArchiveBlocked
    from app.tiered_archive import runtime
    from app.tiered_archive.source import DATASETS, Source

    engine, db, rows, _, waves, _ = fixture(bad_neighbor)
    monkeypatch.setattr(runtime, "direct_wav_reader", lambda _: WavReader(waves.__getitem__))
    monkeypatch.setattr(runtime, "environment", lambda: "staging")
    try:
        adapter = Source(
            None,
            "direct",
            "staging",
            checkpoint=lambda: None,
            payload_reader=runtime.direct_payload_reader,
        )
        with pytest.raises(ArchiveBlocked):
            adapter.context(db, DATASETS["direct_chunk"], rows[0][0])
        assert db.in_transaction()
    finally:
        db.close()
        engine.dispose()


def test_archive_only_original_corruption_is_rejected():
    from app.raw_archive.policy import ArchiveBlocked
    from app.tiered_archive.recovery import archived_direct_original, direct_children

    body = {
        "row": {
            "id": "chunk",
            "payload": None,
            "payload_bytes": 2,
            "payload_sha256": hashlib.sha256(b"ab").hexdigest(),
        },
        "context": {"original_payload": b"ax", "payload_reference": None, "pieces": []},
    }
    with pytest.raises(ArchiveBlocked, match="bytes differ"):
        archived_direct_original(body)
    with pytest.raises(ArchiveBlocked, match="bytes differ"):
        direct_children(body)


def test_corrupt_wav_and_wrong_configuration_block_original_reconstruction(monkeypatch):
    from app.direct.payloads import WavReader
    from app.tiered_archive import runtime

    engine, db, rows, contexts, waves, _ = fixture()
    bad_waves = dict(waves)
    key = next(iter(bad_waves))
    bad_waves[key] = bad_waves[key][:-1]
    monkeypatch.setattr(runtime, "direct_wav_reader", lambda _: WavReader(bad_waves.__getitem__))
    monkeypatch.setattr(runtime, "environment", lambda: "staging")
    try:
        with pytest.raises(ValueError):
            runtime.direct_payload_reader(db, rows[0][0], contexts[0])
        context = copy.deepcopy(contexts[0])
        context["session_config"]["sample_rate"] = 500
        with pytest.raises((ValueError, RuntimeError)):
            runtime.direct_payload_reader(db, rows[0][0], context)
        assert db.in_transaction()
    finally:
        db.close()
        engine.dispose()


def test_canonical_reconstructor_cannot_write_source(monkeypatch):
    from app.direct import derived_original
    from app.direct.payloads import WavReader
    from app.raw_archive.policy import ArchiveBlocked

    engine, db, rows, contexts, waves, _ = fixture()
    try:
        for operation in ("add", "commit", "flush", "delete"):

            def illegal(session, _chunk, operation=operation):
                getattr(session, operation)(None)

            monkeypatch.setattr(derived_original, "prepare_reference", illegal)
            with pytest.raises(ArchiveBlocked, match="cannot modify"):
                derived_original.reconstruct_detached(
                    db, rows[0][0], contexts[0], WavReader(waves.__getitem__)
                )
            assert db.in_transaction()
    finally:
        db.close()
        engine.dispose()


def test_parquet_restore_keeps_null_payload_and_null_reference(monkeypatch, tmp_path):
    from sqlalchemy import text

    from app.direct.payloads import WavReader
    from app.tiered_archive import runtime
    from app.tiered_archive.recovery import archived_direct_original, restore_database
    from app.tiered_archive.source import DATASETS, Source
    from tests.test_tiered_restore import URL, bundle

    source_engine, db, rows, _, waves, _ = fixture()
    reader = WavReader(waves.__getitem__, cache_bytes=1024**2)
    monkeypatch.setattr(runtime, "direct_wav_reader", lambda _: reader)
    monkeypatch.setattr(runtime, "environment", lambda: "staging")
    adapter = Source(
        None,
        "direct",
        "staging",
        checkpoint=lambda: None,
        payload_reader=runtime.direct_payload_reader,
    )
    try:
        bodies = [
            {"row": row, "context": adapter.context(db, DATASETS["direct_chunk"], row)}
            for row, _ in rows
        ]
        folder, manifest_sha = bundle(tmp_path, [bodies], "direct_chunk")
    finally:
        db.close()
        source_engine.dispose()
    target = create_engine("sqlite:///" + str(tmp_path / "restored.sqlite3"))

    @event.listens_for(target, "before_cursor_execute", retval=True)
    def postgres_directives(_conn, _cursor, statement, parameters, _context, _many):
        if statement.startswith(("SET LOCAL ", "LOCK TABLE ")):
            return "SELECT 1", ()
        return statement, parameters

    with target.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE direct_chunk(id TEXT PRIMARY KEY,device_id TEXT,session_id TEXT,sequence INTEGER,first_frame INTEGER,frame_count INTEGER,digest TEXT,payload_sha256 TEXT,payload BLOB,payload_bytes INTEGER,received_at REAL)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE direct_piece(segment_id TEXT,chunk_id TEXT,payload_offset INTEGER,frame_count INTEGER,PRIMARY KEY(segment_id,chunk_id))"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE direct_chunk_payload_ref(chunk_id TEXT PRIMARY KEY,version INTEGER,parts JSON,archived_at REAL)"
            )
        )
    monkeypatch.setattr("sqlalchemy.create_engine", lambda *_args, **_kwargs: target)
    assert restore_database(folder, URL, manifest_sha, checkpoint=lambda: None)[
        "typed_values_verified"
    ]
    with target.connect() as conn:
        values = (
            conn.execute(text("SELECT id,payload,payload_sha256 FROM direct_chunk ORDER BY id"))
            .mappings()
            .all()
        )
        assert len(values) == 2 and all(row["payload"] is None for row in values)
        assert conn.execute(text("SELECT count(*) FROM direct_chunk_payload_ref")).scalar_one() == 0
    assert [archived_direct_original(body) for body in bodies] == [expected for _, expected in rows]
    target.dispose()
