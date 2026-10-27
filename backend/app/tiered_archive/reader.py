"""Read evicted dashboard rows with tenant scope, bounded files and DuckDB."""

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from app.raw_archive.policy import ArchiveBlocked

from .codec import decode, epoch_us


def archived_statistics(
    ledger, cache, store, *, environment, source, organization, dataset, sensor, start, end, step
):
    """Project only moments; year views never decode scientific payload blobs."""
    import duckdb
    import pyarrow as pa
    import pyarrow.parquet as pq

    from .parquet import STAT_TYPES, statistics

    # Retained source buckets are at most ten minutes. Discover a partition
    # ending before start if its final bucket still intersects the window.
    receipts = ledger.scope(
        environment, source, organization, dataset, start - timedelta(seconds=600), end
    )
    if len(receipts) > 2048 or type(step) is not int or not 1 <= step <= 604800:
        raise ArchiveBlocked("Historical query exceeds archive partition budget")
    columns = [
        "identity",
        "organization",
        "sensor",
        "time_us",
        *["stat_" + key for key in STAT_TYPES],
    ]
    schema = pa.schema(
        [
            ("identity", pa.string()),
            ("organization", pa.string()),
            ("sensor", pa.string()),
            ("time_us", pa.int64()),
            *(("stat_" + name, getattr(pa, kind)()) for name, kind in STAT_TYPES.items()),
        ]
    )

    def legacy_batches(partition):
        for batch in partition.iter_batches(batch_size=64):
            rows = []
            for envelope in batch.to_pylist():
                rows.append(
                    {
                        **{key: envelope[key] for key in columns[:4]},
                        **statistics(decode(envelope["body"])),
                    }
                )
            yield pa.RecordBatch.from_pylist(rows, schema=schema)

    try:
        with duckdb.connect(
            config={"threads": "1", "memory_limit": "64MB", "enable_external_access": "false"}
        ) as db:
            db.execute("""CREATE TEMP TABLE moments(identity VARCHAR PRIMARY KEY,kind VARCHAR,unit VARCHAR,
                bucket_us BIGINT,seconds INTEGER,n BIGINT,total DOUBLE,total2 DOUBLE,minimum DOUBLE,maximum DOUBLE,duration DOUBLE)""")
            for receipt in sorted(receipts, key=lambda item: item["published_at"]):
                if receipt["state"] == "deleting":
                    raise ArchiveBlocked("Archive ownership transition pending")
                if receipt["state"] != "evicted":
                    continue
                with cache.file(receipt["files"][0], store) as path:
                    partition = pq.ParquetFile(path)
                    batches = (
                        partition.iter_batches(batch_size=128, columns=columns)
                        if all(name in partition.schema_arrow.names for name in columns)
                        else legacy_batches(partition)
                    )
                    db.register(
                        "cold_partition", pa.RecordBatchReader.from_batches(schema, batches)
                    )
                    db.execute(
                        """INSERT OR REPLACE INTO moments SELECT identity,stat_kind,stat_unit,stat_bucket_us,
                        stat_seconds,stat_n,stat_total,stat_total2,stat_minimum,stat_maximum,stat_duration
                        FROM cold_partition WHERE organization=? AND sensor=? AND time_us>=? AND time_us<?
                        AND stat_n>0 AND stat_seconds>0
                        AND stat_bucket_us<? AND stat_bucket_us+CAST(stat_seconds AS BIGINT)*1000000>?""",
                        [
                            organization,
                            sensor,
                            epoch_us(start - timedelta(seconds=600)),
                            epoch_us(end),
                            epoch_us(end),
                            epoch_us(start),
                        ],
                    )
                    db.unregister("cold_partition")
            cursor = db.execute(
                """SELECT kind,unit,CAST(floor(bucket_us/1000000.0/greatest(seconds,?))*greatest(seconds,?) AS BIGINT) AS bucket,
                greatest(seconds,?) AS seconds,sum(n) AS n,sum(total) AS total,sum(total2) AS total2,
                min(minimum) AS minimum,max(maximum) AS maximum,sum(duration) AS duration
                    FROM moments GROUP BY 1,2,3,4 ORDER BY kind,unit,bucket LIMIT 20001""",
                [step] * 3,
            )
            rows = [
                dict(zip([column[0] for column in cursor.description], row, strict=True))
                for row in cursor.fetchall()
            ]
            if len(rows) > 20000:
                raise ArchiveBlocked("Choose a coarser historical resolution")
            return rows
    except duckdb.Error as exc:
        raise ArchiveBlocked("Historical query exceeds bounded reader resources") from exc


def archived_rows(
    ledger,
    cache,
    store,
    *,
    environment,
    source,
    organization,
    dataset,
    sensor,
    start,
    end,
    maximum_rows=20_000,
    newest=False,
    anomalies_only=False,
):
    import duckdb

    receipts = ledger.scope(
        environment,
        source,
        organization,
        dataset,
        datetime(2000, 1, 1, tzinfo=UTC) if newest else start,
        datetime.now(UTC) if newest else end,
    )
    latest = {}
    if len(receipts) > 2048:
        raise ArchiveBlocked("Historical query exceeds archive partition budget")
    with duckdb.connect(
        config={"threads": "1", "memory_limit": "64MB", "enable_external_access": "false"}
    ) as db:
        # Register each already verified local file as an Arrow stream; DuckDB
        # cannot open arbitrary network/file paths supplied by a request.
        import pyarrow as pa
        import pyarrow.parquet as pq

        for receipt in sorted(receipts, key=lambda item: item["published_at"]):
            if receipt["state"] == "deleting":
                raise ArchiveBlocked("Archive ownership transition pending")
            if receipt["state"] != "evicted":
                continue
            reference = receipt["files"][0]
            with cache.file(reference, store) as path:
                partition = pq.ParquetFile(path)
                db.register(
                    "cold_partition",
                    pa.RecordBatchReader.from_batches(
                        partition.schema_arrow, partition.iter_batches(batch_size=128)
                    ),
                )
                predicate = ""
                values = [
                    organization,
                    sensor,
                    epoch_us(start),
                    epoch_us(end),
                ]
                if newest:
                    predicate = " AND CAST(json_extract_string(decode(body),'$.value.row.value.ended_at.value') AS TIMESTAMPTZ)<=?"
                    values.append(end)
                    if anomalies_only:
                        predicate += " AND json_extract_string(decode(body),'$.value.row.value.is_anomaly')='true'"
                values.append(maximum_rows + 1)
                rows = db.execute(
                    "SELECT identity,body FROM cold_partition WHERE organization=? AND sensor=? AND time_us>=? AND time_us<?"
                    + predicate
                    + " ORDER BY time_us DESC LIMIT ?",
                    values,
                ).fetchall()
                db.unregister("cold_partition")
            for identity, body in rows:
                latest[identity] = decode(body)["row"]
                if len(latest) > maximum_rows:
                    if not newest:
                        raise ArchiveBlocked("Historical query exceeds row budget")
                    oldest = min(latest, key=lambda key: latest[key]["started_at"])
                    del latest[oldest]
    return list(latest.values())


def points(rows, *, source, step):
    totals = {}
    for row in rows:
        stamp = row["bucket"]
        seconds = max(step, row["seconds"])
        epoch = int(stamp.timestamp()) if isinstance(stamp, datetime) else int(stamp)
        bucket = epoch // seconds * seconds
        kind = row.get("kind", f"bio_signal_ch{row.get('channel', 0) + 1}")
        key = (kind, row["unit"], bucket, seconds)
        if key not in totals:
            totals[key] = dict(
                n=0, total=0.0, total2=0.0, minimum=math.inf, maximum=-math.inf, duration=0.0
            )
        value = totals[key]
        for field in ("n", "total", "total2"):
            value[field] += row[field]
        value["minimum"] = min(value["minimum"], row["minimum"])
        value["maximum"] = max(value["maximum"], row["maximum"])
        value["duration"] += row.get("duration", 0.0)
    grouped = defaultdict(list)
    for (kind, unit, bucket, seconds), row in sorted(totals.items()):
        mean = row["total"] / row["n"]
        grouped[kind, unit].append(
            {
                "timestamp": datetime.fromtimestamp(bucket, UTC).isoformat(),
                "value": round(mean, 4),
                "_total": row["total"],
                "_total2": row["total2"],
                "_duration": row["duration"],
                "minimum": row["minimum"],
                "maximum": row["maximum"],
                "reading_count": row["n"],
                "resolution_seconds": seconds,
                "rms": math.sqrt(max(0, row["total2"] / row["n"])),
                "standard_deviation": math.sqrt(max(0, row["total2"] / row["n"] - mean * mean)),
                "coverage_ratio": min(1, row["duration"] / seconds) if row["duration"] else None,
                "signal_source": "wav" if source == "direct" else "readings",
            }
        )
    return [{"kind": kind, "unit": unit, "data": data} for (kind, unit), data in grouped.items()]


def merge(live, cold, sensor, *, source, limit=20000):
    grouped = {}
    for series in [*cold, *live]:
        key = (series["kind"], series["unit"])
        if key not in grouped:
            grouped[key] = dict(
                series,
                sensor_id=str(sensor),
                data=[],
                aggregation="24h:1s;7d:1m;older:10m",
                source=source,
            )
        points_by_key = {(p["timestamp"], p["resolution_seconds"]): p for p in grouped[key]["data"]}
        for point in series["data"]:
            identity = (point["timestamp"], point["resolution_seconds"])
            if identity in points_by_key:
                # Distinct exact rows can share an aggregate bucket while a
                # partition is being drained. Merge moments, never means alone.
                previous = points_by_key[identity]
                n1, n2 = previous["reading_count"], point["reading_count"]
                total = previous.get("_total", previous["value"] * n1) + point.get(
                    "_total", point["value"] * n2
                )
                total2 = previous.get(
                    "_total2", (previous.get("rms") or abs(previous["value"])) ** 2 * n1
                ) + point.get("_total2", (point.get("rms") or abs(point["value"])) ** 2 * n2)
                mean, n = total / (n1 + n2), n1 + n2
                duration = previous.get(
                    "_duration", (previous.get("coverage_ratio") or 0) * point["resolution_seconds"]
                ) + point.get(
                    "_duration", (point.get("coverage_ratio") or 0) * point["resolution_seconds"]
                )
                point = dict(
                    point,
                    reading_count=n,
                    value=round(mean, 4),
                    _total=total,
                    _total2=total2,
                    _duration=duration,
                    coverage_ratio=min(1, duration / point["resolution_seconds"])
                    if duration
                    else point.get("coverage_ratio"),
                    minimum=min(
                        previous["minimum"]
                        if previous.get("minimum") is not None
                        else previous["value"],
                        point["minimum"] if point.get("minimum") is not None else point["value"],
                    ),
                    maximum=max(
                        previous["maximum"]
                        if previous.get("maximum") is not None
                        else previous["value"],
                        point["maximum"] if point.get("maximum") is not None else point["value"],
                    ),
                    rms=math.sqrt(max(0, total2 / n)),
                    standard_deviation=math.sqrt(max(0, total2 / n - mean * mean)),
                )
                if source == "direct" and duration > point["resolution_seconds"] * 1.02:
                    point["signal_source"] = "overlap"
            points_by_key[identity] = point
        grouped[key]["data"] = sorted(points_by_key.values(), key=lambda point: point["timestamp"])
        if len(grouped[key]["data"]) >= limit:
            raise ArchiveBlocked("Choose a coarser historical resolution")
    return list(grouped.values())


def history(live, db, sensor, start, end, step, *, source, limit=20000):
    """Called only after the existing route has authorized this sensor/device."""
    import os

    if os.getenv("TIERED_ARCHIVE_READS_ENABLED", "false") != "true":
        return strip_internal(live)
    from sqlalchemy import text

    if source == "gateway":
        organization = db.execute(
            text("""SELECT z.organization_id FROM sensor s
            JOIN gateway g ON g.id=s.gateway_id JOIN zone z ON z.id=g.zone_id WHERE s.id=:id"""),
            {"id": sensor},
        ).scalar_one()
        dataset = "visual_reading"
    else:
        organization = db.execute(
            text("SELECT organization_id FROM direct_device WHERE id=:id"), {"id": str(sensor)}
        ).scalar_one()
        dataset = "direct_visual_point"
    if os.getenv("TIERED_ARCHIVE_READ_BROKER_SOCKET") or os.getenv(
        "RAW_ARCHIVE_READ_BROKER_SOCKET"
    ):
        from .client import request

        connection, response = request(
            "/tiered-series",
            dict(
                source=source,
                organization=str(organization),
                sensor=str(sensor),
                start=start.isoformat(),
                end=end.isoformat(),
                step=step,
            ),
            timeout=30,
        )
        try:
            import json

            raw = response.read(4 * 1024**2 + 1)
            if len(raw) > 4 * 1024**2:
                raise ArchiveBlocked("Historical response exceeds budget")
            cold = json.loads(raw)
            if not isinstance(cold, dict) or set(cold) != {"series", "waves"}:
                raise ArchiveBlocked("Invalid historical response")
            return strip_internal(
                apply_waves(
                    merge(live, cold["series"], sensor, source=source, limit=limit), cold["waves"]
                )
            )
        finally:
            connection.close()
    from .runtime import reader_resources

    ledger, cache, store = reader_resources()
    try:
        cold = cold_data(
            ledger,
            cache,
            store,
            environment=os.environ["TIERED_ARCHIVE_ENVIRONMENT"],
            source=source,
            organization=str(organization),
            dataset=dataset,
            sensor=str(sensor),
            start=start,
            end=end,
            step=step,
        )
        return strip_internal(
            apply_waves(
                merge(live, cold["series"], sensor, source=source, limit=limit), cold["waves"]
            )
        )
    finally:
        ledger.close()


def strip_internal(series):
    for item in series:
        for point in item["data"]:
            point.pop("_total", None)
            point.pop("_total2", None)
            point.pop("_duration", None)
            point.pop("_wave", None)
    return series


def cold_data(
    ledger, cache, store, *, environment, source, organization, dataset, sensor, start, end, step
):
    scope = dict(
        environment=environment,
        source=source,
        organization=organization,
        sensor=sensor,
        start=start,
        end=end,
        step=step,
    )

    def intersects(row):
        stamp = row["bucket"].timestamp() if isinstance(row["bucket"], datetime) else row["bucket"]
        return stamp < end.timestamp() and stamp + row["seconds"] > start.timestamp()

    rows = [
        row
        for row in archived_statistics(ledger, cache, store, dataset=dataset, **scope)
        if intersects(row)
    ]
    waves = []
    if source == "gateway":
        raw = [
            dict(row, kind="bio_signal", unit="mV")
            for row in archived_statistics(ledger, cache, store, dataset="visual_wave", **scope)
            if intersects(row)
        ]
        waves = points(raw, source="gateway", step=step)
    return {"series": points(rows, source=source, step=step), "waves": waves}


def apply_waves(series, waves):
    lookup = {
        (point["timestamp"], point["resolution_seconds"]): point
        for item in waves
        for point in item["data"]
    }
    for item in series:
        if item["kind"] not in {"bio_signal", "bioelectric"} or item["unit"] != "mV":
            continue
        for point in item["data"]:
            wave = lookup.get((point["timestamp"], point["resolution_seconds"]))
            if wave is None:
                continue
            observed = dict(
                point.get("_wave")
                or {
                    "n": 0,
                    "total": 0.0,
                    "total2": 0.0,
                    "minimum": math.inf,
                    "maximum": -math.inf,
                    "duration": 0.0,
                }
            )
            observed["n"] += wave["reading_count"]
            observed["total"] += wave["_total"]
            observed["total2"] += wave["_total2"]
            observed["minimum"] = min(observed["minimum"], wave["minimum"])
            observed["maximum"] = max(observed["maximum"], wave["maximum"])
            observed["duration"] += wave["_duration"]
            coverage = observed["duration"] / point["resolution_seconds"]
            if coverage > 1.02:
                point["signal_source"] = "overlap"
            else:
                mean = observed["total"] / observed["n"]
                point.update(
                    minimum=round(observed["minimum"], 4),
                    maximum=round(observed["maximum"], 4),
                    rms=round(math.sqrt(max(0, observed["total2"] / observed["n"])), 4),
                    standard_deviation=round(
                        math.sqrt(max(0, observed["total2"] / observed["n"] - mean * mean)), 4
                    ),
                    coverage_ratio=min(1, coverage),
                    signal_source="wav",
                )
    return series


def private_series(data, destination):
    import uuid

    from .ledger import Ledger
    from .policy import utc
    from .runtime import environment, state_root
    from .storage import Cache, Store

    if not isinstance(data, dict) or set(data) != {
        "source",
        "organization",
        "sensor",
        "start",
        "end",
        "step",
    }:
        raise ArchiveBlocked("Exact historical scope required")
    source = data["source"]
    if (
        source not in {"gateway", "direct"}
        or type(data["step"]) is not int
        or not 1 <= data["step"] <= 86400 * 7
    ):
        raise ArchiveBlocked("Invalid historical source or resolution")
    organization, sensor = str(uuid.UUID(data["organization"])), str(uuid.UUID(data["sensor"]))
    start, end = utc(data["start"]), utc(data["end"])
    if start >= end or (end - start).days > 3660:
        raise ArchiveBlocked("Historical interval exceeds budget")
    ledger = Ledger(state_root(), read_only=True)
    try:
        return cold_data(
            ledger,
            Cache(state_root() / "cache"),
            Store(destination),
            environment=environment(),
            source=source,
            organization=organization,
            dataset="visual_reading" if source == "gateway" else "direct_visual_point",
            sensor=sensor,
            start=start,
            end=end,
            step=data["step"],
        )
    finally:
        ledger.close()
