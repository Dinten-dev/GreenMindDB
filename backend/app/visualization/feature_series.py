"""Coarse signal summaries from verified complete WAVs, never invented subwindows."""

import math

from sqlalchemy import text


def series(db, sensor_id, start, end, step, limit):
    # Preserve the raw path for environmental/mixed sensors. Looking backwards
    # over the timestamp primary key avoids scanning the full sensor history.
    kinds = db.execute(
        text("""SELECT DISTINCT kind,unit FROM (
      SELECT kind,unit FROM sensor_reading WHERE sensor_id=:sid
      ORDER BY timestamp DESC LIMIT 100) recent"""),
        {"sid": sensor_id},
    ).all()
    if len(kinds) != 1 or kinds[0][0] not in {"bio_signal", "bioelectric"} or kinds[0][1] != "mV":
        return None
    rows = (
        db.execute(
            text("""WITH recordings AS (
      SELECT w.id,w.started_at,w.ended_at,f.sample_count AS n,f.duration_seconds,
        f.mean,f.rms,f.minimum,f.maximum,
        lag(w.ended_at) OVER (ORDER BY w.started_at,w.id) AS previous_end
      FROM wav_file w JOIN wav_feature f ON f.wav_file_id=w.id
      WHERE w.sensor_id=:sid AND w.started_at>=:start AND w.ended_at<=:end
        AND w.feature_status='verified' AND f.verified_at IS NOT NULL
        AND f.source_sha256=w.content_sha256 AND f.value_unit='mV'
        AND f.calibration_version=w.calibration_version AND f.sample_rate=w.sample_rate
        AND f.sample_count>0 AND f.duration_seconds>0
        AND f.coverage_ratio>=0.999 AND w.coverage_ratio>=0.999
        AND f.timing_status IN ('complete','inferred')
    ) SELECT floor(extract(epoch FROM started_at+(ended_at-started_at)/2)/:step) AS bin,
      min(started_at) AS first,max(ended_at) AS last,sum(n) AS n,
      sum(n*mean)/sum(n) AS mean,sum(n*rms*rms)/sum(n) AS square_mean,
      min(minimum) AS minimum,max(maximum) AS maximum,
      sum(duration_seconds) AS duration,count(*) AS recordings,
      sum(greatest(0,extract(epoch FROM previous_end-started_at))) AS overlap_seconds
    FROM recordings
    GROUP BY bin ORDER BY bin LIMIT :lim"""),
            dict(sid=sensor_id, start=start, end=end, step=step, lim=limit),
        )
        .mappings()
        .all()
    )
    if not rows:
        return None
    points = []
    for row in rows:
        span = (row["last"] - row["first"]).total_seconds()
        if span <= 0 or any(
            not math.isfinite(row[key])
            for key in ("mean", "square_mean", "minimum", "maximum", "duration")
        ):
            raise ValueError("Invalid verified feature summary")
        points.append(
            dict(
                timestamp=row["first"].isoformat(),
                value=round(row["mean"], 4),
                resolution_seconds=max(step, math.ceil(span)),
                reading_count=int(row["n"]),
                minimum=round(row["minimum"], 4),
                maximum=round(row["maximum"], 4),
                rms=round(math.sqrt(max(0, row["square_mean"])), 4),
                standard_deviation=round(
                    math.sqrt(max(0, row["square_mean"] - row["mean"] ** 2)), 4
                ),
                coverage_ratio=None
                if row["overlap_seconds"] > 0
                else min(1, row["duration"] / span),
                timing_overlap_seconds=float(row["overlap_seconds"]),
                signal_source="wav_features",
                source_interval_start=row["first"].isoformat(),
                source_interval_end=row["last"].isoformat(),
                recording_count=row["recordings"],
            )
        )
    return [
        dict(
            sensor_id=str(sensor_id),
            kind=kinds[0][0],
            unit="mV",
            data=points,
            aggregation="sample-weighted verified complete WAV summaries; actual recording boundaries",
            original_signal_available=True,
        )
    ]
