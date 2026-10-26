"""Cold scientific features behind the same sensor/zone authorization."""

import os
from datetime import UTC, datetime
from types import SimpleNamespace

from app.raw_archive.policy import ArchiveBlocked


def private_features(data, destination):
    import uuid

    from .ledger import Ledger
    from .reader import archived_rows
    from .runtime import environment, state_root
    from .storage import Cache, Store

    if not isinstance(data, dict) or set(data) != {
        "organization",
        "sensor",
        "start",
        "end",
        "limit",
        "anomalies_only",
    }:
        raise ArchiveBlocked("Exact feature scope required")
    if (
        type(data["limit"]) is not int
        or not 1 <= data["limit"] <= 1000
        or type(data["anomalies_only"]) is not bool
    ):
        raise ArchiveBlocked("Bounded feature query required")
    from .policy import utc

    organization, sensor = str(uuid.UUID(data["organization"])), str(uuid.UUID(data["sensor"]))
    ledger = Ledger(state_root(), read_only=True)
    try:
        return archived_rows(
            ledger,
            Cache(state_root() / "cache"),
            Store(destination),
            environment=environment(),
            source="gateway",
            organization=organization,
            dataset="wav_feature",
            sensor=sensor,
            start=utc(data["start"]),
            end=utc(data["end"]),
            maximum_rows=data["limit"],
            newest=True,
            anomalies_only=data["anomalies_only"],
        )
    finally:
        ledger.close()


def history(
    live, db, user, *, sensor_id=None, from_dt=None, to_dt=None, anomalies_only=False, limit=100
):
    if os.getenv("TIERED_ARCHIVE_READS_ENABLED", "false") != "true":
        return live
    import json

    from app.models.master import Gateway, Sensor, Zone
    from app.zone_access import zone_access_filter

    from .client import request
    from .codec import unpack

    def bound(value, default):
        if not value:
            return default
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

    start = bound(from_dt, datetime(2000, 1, 1, tzinfo=UTC))
    end = bound(to_dt, datetime.now(UTC))

    query = (
        db.query(Sensor.id, Zone.organization_id)
        .join(Gateway, Gateway.id == Sensor.gateway_id)
        .join(Zone, Zone.id == Gateway.zone_id)
        .filter(zone_access_filter(user))
    )
    if sensor_id:
        query = query.filter(Sensor.id == sensor_id)
    sensors = query.limit(1001).all()
    if len(sensors) > 1000:
        raise ArchiveBlocked("Select a sensor before historical feature lookup")
    merged = {str(row.id): row for row in live}
    for identity, organization in sensors:
        connection, response = request(
            "/tiered-features",
            {
                "sensor": str(identity),
                "organization": str(organization),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": limit,
                "anomalies_only": anomalies_only,
            },
            timeout=30,
        )
        try:
            raw = response.read(4 * 1024**2 + 1)
            if len(raw) > 4 * 1024**2:
                raise ArchiveBlocked("Feature response exceeds budget")
            rows = unpack(json.loads(raw))
        finally:
            connection.close()
        for row in rows:
            if str(row["sensor_id"]) != str(identity):
                raise ArchiveBlocked("Feature sensor mismatch")
            if row["started_at"] < start:
                continue
            if row["ended_at"] > end:
                continue
            if anomalies_only and not row["is_anomaly"]:
                continue
            merged.setdefault(str(row["id"]), SimpleNamespace(**row))
    return sorted(merged.values(), key=lambda row: row.started_at, reverse=True)[:limit]
