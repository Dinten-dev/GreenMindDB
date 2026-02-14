"""
1 Hz Time Synchronization & Resampling Service

This module provides deterministic resampling of plant telemetry data
to a canonical 1 Hz time grid for ML training.

Resampling Rules:
- All timestamps floored to second (12:00:00.923 → 12:00:00)
- All times in UTC
- Low-freq sensors: last value within second, forward-fill max 5 min
- Bioelectric: aggregate all samples in [t, t+1s) → mean, std
"""
import math
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Tuple, Any
from dataclasses import dataclass

from sqlalchemy import text, select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import Species, Metric, TelemetryChannel, TelemetryMeasurement
from app.models.plant_state import PlantState1Hz, ResamplingState

logger = logging.getLogger(__name__)

# Configuration
MAX_FORWARD_FILL_SECONDS = 5 * 60  # 5 minutes max forward-fill gap
BATCH_SIZE = 1000  # Max rows to insert at once

# Metric key to column name mapping
METRIC_COLUMN_MAP = {
    "air_temperature_c": "air_temperature_c",
    "rel_humidity_pct": "rel_humidity_pct",
    "light_ppfd_umol_m2_s": "light_ppfd",
    "soil_moisture_vwc_pct": "soil_moisture_pct",
    "soil_ph": "soil_ph",
    "bioelectric_voltage_mv": None,  # Special handling for bioelectric
}


@dataclass
class SensorReading:
    """A single sensor reading with timestamp and value."""
    timestamp: datetime
    value: float
    quality: int = 0


def floor_to_second(dt: datetime) -> datetime:
    """Floor a datetime to the nearest second (UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.replace(microsecond=0)


def generate_time_grid(start: datetime, end: datetime) -> List[datetime]:
    """
    Generate a canonical 1 Hz time grid from start to end (inclusive).
    
    Args:
        start: Start timestamp (will be floored to second)
        end: End timestamp (will be floored to second)
    
    Returns:
        List of UTC timestamps at 1-second intervals
    """
    start = floor_to_second(start)
    end = floor_to_second(end)
    
    if start > end:
        return []
    
    grid = []
    current = start
    while current <= end:
        grid.append(current)
        current += timedelta(seconds=1)
    
    return grid


def resample_low_freq_metric(
    readings: List[SensorReading],
    time_grid: List[datetime],
    max_gap_seconds: int = MAX_FORWARD_FILL_SECONDS
) -> Dict[datetime, Optional[float]]:
    """
    Resample low-frequency sensor data to the time grid using forward-fill.
    
    Strategy:
    - If multiple values in the same second: take the last one
    - If no value in a second: forward-fill from previous value
    - If gap exceeds max_gap_seconds: set to NULL
    
    Args:
        readings: List of sensor readings (should be sorted by timestamp)
        time_grid: Target 1 Hz time grid
        max_gap_seconds: Maximum seconds to forward-fill
    
    Returns:
        Dictionary mapping grid timestamps to values (or None for missing)
    """
    if not time_grid:
        return {}
    
    # Sort readings by timestamp and floor to seconds
    processed_readings: Dict[datetime, float] = {}
    for r in readings:
        ts = floor_to_second(r.timestamp)
        # Last value in same second wins
        processed_readings[ts] = r.value
    
    result: Dict[datetime, Optional[float]] = {}
    last_value: Optional[float] = None
    last_value_ts: Optional[datetime] = None
    
    for grid_ts in time_grid:
        if grid_ts in processed_readings:
            # We have a reading for this exact second
            last_value = processed_readings[grid_ts]
            last_value_ts = grid_ts
            result[grid_ts] = last_value
        elif last_value is not None and last_value_ts is not None:
            # Forward-fill if within max gap
            gap = (grid_ts - last_value_ts).total_seconds()
            if gap <= max_gap_seconds:
                result[grid_ts] = last_value
            else:
                result[grid_ts] = None
        else:
            result[grid_ts] = None
    
    return result


def aggregate_bioelectric(
    readings: List[SensorReading],
    time_grid: List[datetime]
) -> Dict[datetime, Tuple[Optional[float], Optional[float]]]:
    """
    Aggregate high-frequency bioelectric data to 1 Hz.
    
    For each second window [t, t+1s), compute:
    - mean of all samples
    - std (standard deviation) of all samples
    
    Args:
        readings: List of bioelectric readings
        time_grid: Target 1 Hz time grid
    
    Returns:
        Dictionary mapping grid timestamps to (mean, std) tuples
    """
    if not time_grid or not readings:
        return {ts: (None, None) for ts in time_grid}
    
    # Group readings by floored second
    buckets: Dict[datetime, List[float]] = {}
    for r in readings:
        ts = floor_to_second(r.timestamp)
        if ts not in buckets:
            buckets[ts] = []
        buckets[ts].append(r.value)
    
    result: Dict[datetime, Tuple[Optional[float], Optional[float]]] = {}
    
    for grid_ts in time_grid:
        if grid_ts in buckets and buckets[grid_ts]:
            values = buckets[grid_ts]
            n = len(values)
            mean_val = sum(values) / n
            
            if n > 1:
                # Sample standard deviation
                variance = sum((x - mean_val) ** 2 for x in values) / (n - 1)
                std_val = math.sqrt(variance)
            else:
                std_val = 0.0
            
            result[grid_ts] = (mean_val, std_val)
        else:
            result[grid_ts] = (None, None)
    
    return result


def get_or_create_resampling_state(db: Session, species_id: int) -> ResamplingState:
    """Get or create resampling state for a species."""
    state = db.query(ResamplingState).filter(
        ResamplingState.species_id == species_id
    ).first()
    
    if not state:
        state = ResamplingState(
            species_id=species_id,
            last_processed_ts=datetime(1970, 1, 1, tzinfo=timezone.utc)
        )
        db.add(state)
        db.flush()
    
    return state


def fetch_raw_measurements(
    db: Session,
    channel_id: str,
    start_ts: datetime,
    end_ts: datetime
) -> List[SensorReading]:
    """Fetch raw measurements from telemetry_measurement table."""
    query = text("""
        SELECT time, value, quality
        FROM telemetry_measurement
        WHERE channel_id = :channel_id
          AND time >= :start_ts
          AND time < :end_ts
        ORDER BY time ASC
    """)
    
    rows = db.execute(query, {
        "channel_id": channel_id,
        "start_ts": start_ts,
        "end_ts": end_ts
    }).fetchall()
    
    return [SensorReading(timestamp=r.time, value=r.value, quality=r.quality) for r in rows]


def run_pipeline_for_species(
    db: Session,
    species_id: int,
    current_time: Optional[datetime] = None
) -> int:
    """
    Run the resampling pipeline for a single species.
    
    Args:
        db: Database session
        species_id: Species to process
        current_time: Override for current time (for testing)
    
    Returns:
        Number of rows inserted/updated
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    
    current_time = floor_to_second(current_time)
    
    # Get or create resampling state
    state = get_or_create_resampling_state(db, species_id)
    start_ts = state.last_processed_ts
    
    # Don't process if no new data window
    if start_ts >= current_time:
        return 0
    
    # Process in reasonable chunks (max 1 hour at a time)
    end_ts = min(start_ts + timedelta(hours=1), current_time)
    
    # Generate 1 Hz time grid
    time_grid = generate_time_grid(start_ts, end_ts - timedelta(seconds=1))
    
    if not time_grid:
        return 0
    
    logger.info(f"Processing species {species_id}: {start_ts} to {end_ts} ({len(time_grid)} seconds)")
    
    # Get all telemetry channels for this species
    channels_query = text("""
        SELECT tc.id, m.key as metric_key
        FROM telemetry_channel tc
        JOIN metric m ON tc.metric_id = m.id
        WHERE tc.species_id = :species_id
    """)
    channels = db.execute(channels_query, {"species_id": species_id}).fetchall()
    channel_map = {ch.metric_key: str(ch.id) for ch in channels}
    
    # Prepare data containers for each grid point
    grid_data: Dict[datetime, Dict[str, Any]] = {ts: {} for ts in time_grid}
    
    # Process each metric
    for metric_key, column_name in METRIC_COLUMN_MAP.items():
        if metric_key not in channel_map:
            continue
        
        channel_id = channel_map[metric_key]
        readings = fetch_raw_measurements(db, channel_id, start_ts, end_ts)
        
        if metric_key == "bioelectric_voltage_mv":
            # Special aggregation for bioelectric
            aggregated = aggregate_bioelectric(readings, time_grid)
            for ts, (mean_val, std_val) in aggregated.items():
                grid_data[ts]["bio_voltage_mean"] = mean_val
                grid_data[ts]["bio_voltage_std"] = std_val
        else:
            # Standard forward-fill for low-freq sensors
            resampled = resample_low_freq_metric(readings, time_grid)
            for ts, value in resampled.items():
                grid_data[ts][column_name] = value
    
    # Build quality flags
    for ts in time_grid:
        flags = {}
        for col, val in grid_data[ts].items():
            if val is None:
                flags[col] = "missing"
        grid_data[ts]["quality_flags"] = flags if flags else {}
    
    # Upsert rows into plant_state_1hz
    rows_to_insert = []
    for ts in time_grid:
        row = {
            "timestamp": ts,
            "species_id": species_id,
            "air_temperature_c": grid_data[ts].get("air_temperature_c"),
            "rel_humidity_pct": grid_data[ts].get("rel_humidity_pct"),
            "light_ppfd": grid_data[ts].get("light_ppfd"),
            "soil_moisture_pct": grid_data[ts].get("soil_moisture_pct"),
            "soil_ph": grid_data[ts].get("soil_ph"),
            "bio_voltage_mean": grid_data[ts].get("bio_voltage_mean"),
            "bio_voltage_std": grid_data[ts].get("bio_voltage_std"),
            "quality_flags": grid_data[ts].get("quality_flags", {}),
        }
        rows_to_insert.append(row)
    
    # Batch insert with ON CONFLICT update
    inserted_count = 0
    for i in range(0, len(rows_to_insert), BATCH_SIZE):
        batch = rows_to_insert[i:i + BATCH_SIZE]
        
        stmt = pg_insert(PlantState1Hz).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=['species_id', 'timestamp'],
            set_={
                "air_temperature_c": stmt.excluded.air_temperature_c,
                "rel_humidity_pct": stmt.excluded.rel_humidity_pct,
                "light_ppfd": stmt.excluded.light_ppfd,
                "soil_moisture_pct": stmt.excluded.soil_moisture_pct,
                "soil_ph": stmt.excluded.soil_ph,
                "bio_voltage_mean": stmt.excluded.bio_voltage_mean,
                "bio_voltage_std": stmt.excluded.bio_voltage_std,
                "quality_flags": stmt.excluded.quality_flags,
            }
        )
        db.execute(stmt)
        inserted_count += len(batch)
    
    # Update resampling state
    state.last_processed_ts = end_ts
    state.updated_at = datetime.now(timezone.utc)
    
    db.commit()
    
    logger.info(f"Inserted/updated {inserted_count} rows for species {species_id}")
    
    return inserted_count


def run_full_pipeline(db: Session) -> Dict[int, int]:
    """
    Run the resampling pipeline for all species.
    
    Returns:
        Dictionary mapping species_id to rows processed
    """
    # Get all species
    species_list = db.query(Species).all()
    
    results = {}
    for species in species_list:
        try:
            count = run_pipeline_for_species(db, species.id)
            results[species.id] = count
        except Exception as e:
            logger.error(f"Error processing species {species.id}: {e}")
            results[species.id] = -1
    
    return results
