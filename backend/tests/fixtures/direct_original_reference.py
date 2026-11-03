"""Real canonical ORM derivation and byte reconstruction; no accepted-ref writes."""

import hashlib
import io
import json
import wave
from uuid import UUID

from sqlalchemy import create_engine, event, select

from app.direct.models import Base, Chunk, Device, Piece, Revision, Segment, Session
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
