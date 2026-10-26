"""Compact replay fences and aggregate references, never replacement originals."""

import os
from collections import defaultdict
from datetime import UTC

from sqlalchemy import text

from app.raw_archive.policy import ArchiveBlocked

from .codec import epoch_us


def ready(db):
    return db.execute(
        text("""SELECT to_regclass('public.tiered_visual_reference') IS NOT NULL
        AND to_regclass('public.tiered_wave_reference') IS NOT NULL
        AND to_regclass('public.tiered_reading_identity') IS NOT NULL
        AND EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('public.sensor_reading')
        AND tgname='tiered_reading_deduplicate' AND tgenabled IN ('O','A'))""")
    ).scalar_one()


def fence(db, rows):
    keys = sorted(
        {
            (str(row["sensor_id"]), row["kind"], row["timestamp"].astimezone(UTC).date())
            for row in rows
        }
    )
    for sensor, kind, day in keys:
        if not db.execute(
            text(
                "SELECT pg_try_advisory_xact_lock(public.tiered_reading_lock_key(CAST(:sensor AS uuid),:kind,:day))"
            ),
            dict(sensor=sensor, kind=kind, day=day),
        ).scalar_one():
            raise ArchiveBlocked("Historical ingestion is using this exact source scope")


def preserve(db, dataset, rows, identity):
    if dataset == "sensor_reading":
        grouped = defaultdict(list)
        for row in rows:
            grouped[
                str(row["sensor_id"]), row["kind"], row["timestamp"].astimezone(UTC).date()
            ].append(epoch_us(row["timestamp"]))
        for (sensor, kind, day), identities in grouped.items():
            db.execute(
                text("""INSERT INTO public.tiered_reading_identity VALUES(:candidate,CAST(:sensor AS uuid),:kind,:day,:identities)
                ON CONFLICT(archive_candidate,sensor_id,kind,source_day) DO NOTHING"""),
                dict(
                    candidate=identity,
                    sensor=sensor,
                    kind=kind,
                    day=day,
                    identities=sorted(identities),
                ),
            )
    elif dataset in {"visual_reading", "visual_wave"}:
        table = (
            "tiered_visual_reference" if dataset == "visual_reading" else "tiered_wave_reference"
        )
        keys = (
            ["sensor_id", "kind", "bucket", "seconds"]
            if dataset == "visual_reading"
            else ["wav_id", "bucket", "seconds"]
        )
        fields = (
            keys
            + (
                ["unit", "minimum", "maximum", "identities"]
                if dataset == "visual_reading"
                else ["duration"]
            )
            + ["n", "total", "total2"]
        )
        updates = ",".join(name + "=EXCLUDED." + name for name in fields if name not in keys)
        for row in rows:
            db.execute(
                text(
                    "INSERT INTO public."
                    + table
                    + "("
                    + ",".join(fields)
                    + ",archive_candidate) VALUES ("
                    + ",".join(":" + name for name in fields)
                    + ",:archive_candidate) ON CONFLICT("
                    + ",".join(keys)
                    + ") DO UPDATE SET "
                    + updates
                    + ",archive_candidate=EXCLUDED.archive_candidate"
                ),
                {**{key: row[key] for key in fields}, "archive_candidate": identity},
            )


def previous(db, sensor, kind, bucket, seconds):
    if os.getenv("TIERED_ARCHIVE_READS_ENABLED", "false") != "true":
        return None
    return (
        db.execute(
            text(
                "SELECT * FROM public.tiered_visual_reference WHERE sensor_id=:sensor AND kind=:kind AND bucket=:bucket AND seconds=:seconds"
            ),
            dict(sensor=sensor, kind=kind, bucket=bucket, seconds=seconds),
        )
        .mappings()
        .first()
    )


def relation(*, waves=False):
    original = "visual_wave" if waves else "visual_reading"
    if os.getenv("TIERED_ARCHIVE_READS_ENABLED", "false") != "true":
        return original
    reference = "tiered_wave_reference" if waves else "tiered_visual_reference"
    key = "v.wav_id=b.wav_id" if waves else "v.sensor_id=b.sensor_id AND v.kind=b.kind"
    fields = (
        "v.wav_id,v.sensor_id,v.duration-coalesce(b.duration,0) AS duration"
        if waves
        else "v.sensor_id,v.kind,v.unit"
    )
    return f"""(SELECT {fields},v.bucket,v.seconds,v.minimum,v.maximum,
        v.n-coalesce(b.n,0) AS n,v.total-coalesce(b.total,0) AS total,v.total2-coalesce(b.total2,0) AS total2
        FROM {original} v LEFT JOIN public.{reference} b ON {key} AND v.bucket=b.bucket AND v.seconds=b.seconds
        WHERE v.n>coalesce(b.n,0))"""
