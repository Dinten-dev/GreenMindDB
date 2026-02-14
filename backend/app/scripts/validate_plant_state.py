#!/usr/bin/env python3
"""
Validation script for plant_state_1hz data.

Checks:
1. No gaps in time grid (every second covered)
2. All columns numeric or NULL
3. Min/max values within expected bounds
4. Each species has continuous time series
"""
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple, Optional

from sqlalchemy import text, create_engine
from sqlalchemy.orm import sessionmaker

from app.database import SessionLocal
from app.models import Species


def check_time_gaps(db, species_id: int) -> Tuple[bool, List[str]]:
    """Check for gaps in the 1 Hz time grid for a species."""
    errors = []
    
    # Get all timestamps ordered
    result = db.execute(text("""
        SELECT timestamp 
        FROM plant_state_1hz 
        WHERE species_id = :species_id 
        ORDER BY timestamp ASC
    """), {"species_id": species_id}).fetchall()
    
    if len(result) < 2:
        return True, ["Not enough data points to check gaps"]
    
    timestamps = [r.timestamp for r in result]
    gap_count = 0
    max_gap = timedelta(seconds=0)
    
    for i in range(1, len(timestamps)):
        delta = timestamps[i] - timestamps[i-1]
        if delta != timedelta(seconds=1):
            gap_count += 1
            if delta > max_gap:
                max_gap = delta
            if gap_count <= 5:  # Only report first 5 gaps
                errors.append(f"Gap at {timestamps[i-1]}: {delta.total_seconds()}s")
    
    if gap_count > 5:
        errors.append(f"... and {gap_count - 5} more gaps")
    
    if errors:
        errors.insert(0, f"Max gap: {max_gap.total_seconds()}s")
    
    return gap_count == 0, errors


def check_numeric_values(db, species_id: int) -> Tuple[bool, List[str]]:
    """Check that all feature columns are numeric or NULL."""
    errors = []
    
    columns = [
        "air_temperature_c",
        "rel_humidity_pct",
        "light_ppfd",
        "soil_moisture_pct",
        "soil_ph",
        "bio_voltage_mean",
        "bio_voltage_std"
    ]
    
    for col in columns:
        # Check for non-numeric values (would be caught by DB type, but check NaN/Inf)
        result = db.execute(text(f"""
            SELECT COUNT(*) as cnt
            FROM plant_state_1hz 
            WHERE species_id = :species_id 
              AND {col} IS NOT NULL
              AND NOT (
                {col}::float = {col}::float  -- This would be false for NaN
              )
        """), {"species_id": species_id}).fetchone()
        
        if result.cnt > 0:
            errors.append(f"{col}: {result.cnt} non-numeric values found")
    
    return len(errors) == 0, errors


def check_value_bounds(db, species_id: int) -> Tuple[bool, List[str]]:
    """Check that values are within reasonable physical bounds."""
    warnings = []
    
    bounds = {
        "air_temperature_c": (-50, 60),
        "rel_humidity_pct": (0, 100),
        "light_ppfd": (0, 3000),
        "soil_moisture_pct": (0, 100),
        "soil_ph": (0, 14),
        "bio_voltage_mean": (-100, 100),
        "bio_voltage_std": (0, 100),
    }
    
    for col, (min_val, max_val) in bounds.items():
        result = db.execute(text(f"""
            SELECT 
                MIN({col}) as min_val,
                MAX({col}) as max_val,
                COUNT(*) FILTER (WHERE {col} < :min_val OR {col} > :max_val) as out_of_bounds
            FROM plant_state_1hz 
            WHERE species_id = :species_id
        """), {"species_id": species_id, "min_val": min_val, "max_val": max_val}).fetchone()
        
        if result.out_of_bounds and result.out_of_bounds > 0:
            warnings.append(
                f"{col}: {result.out_of_bounds} values out of bounds "
                f"[{min_val}, {max_val}], actual range: [{result.min_val}, {result.max_val}]"
            )
    
    return len(warnings) == 0, warnings


def check_data_coverage(db, species_id: int) -> Dict[str, float]:
    """Calculate data coverage (% non-NULL) for each column."""
    result = db.execute(text("""
        SELECT 
            COUNT(*) as total,
            COUNT(air_temperature_c) as temp_count,
            COUNT(rel_humidity_pct) as humidity_count,
            COUNT(light_ppfd) as light_count,
            COUNT(soil_moisture_pct) as moisture_count,
            COUNT(soil_ph) as ph_count,
            COUNT(bio_voltage_mean) as bio_mean_count,
            COUNT(bio_voltage_std) as bio_std_count
        FROM plant_state_1hz 
        WHERE species_id = :species_id
    """), {"species_id": species_id}).fetchone()
    
    if result.total == 0:
        return {}
    
    coverage = {
        "air_temperature_c": 100 * result.temp_count / result.total,
        "rel_humidity_pct": 100 * result.humidity_count / result.total,
        "light_ppfd": 100 * result.light_count / result.total,
        "soil_moisture_pct": 100 * result.moisture_count / result.total,
        "soil_ph": 100 * result.ph_count / result.total,
        "bio_voltage_mean": 100 * result.bio_mean_count / result.total,
        "bio_voltage_std": 100 * result.bio_std_count / result.total,
    }
    
    return coverage


def validate_species(db, species_id: int, species_name: str) -> bool:
    """Run all validation checks for a species."""
    print(f"\n{'='*60}")
    print(f"Validating: {species_name} (ID: {species_id})")
    print("="*60)
    
    # Get row count
    count = db.execute(text("""
        SELECT COUNT(*) FROM plant_state_1hz WHERE species_id = :species_id
    """), {"species_id": species_id}).scalar()
    
    print(f"Total rows: {count}")
    
    if count == 0:
        print("⚠️  No data found for this species")
        return True
    
    all_passed = True
    
    # 1. Check time gaps
    print("\n1. Time Gap Check:")
    passed, errors = check_time_gaps(db, species_id)
    if passed:
        print("   ✓ No gaps in time grid")
    else:
        all_passed = False
        for e in errors:
            print(f"   ✗ {e}")
    
    # 2. Check numeric values
    print("\n2. Numeric Values Check:")
    passed, errors = check_numeric_values(db, species_id)
    if passed:
        print("   ✓ All values are numeric or NULL")
    else:
        all_passed = False
        for e in errors:
            print(f"   ✗ {e}")
    
    # 3. Check value bounds
    print("\n3. Value Bounds Check:")
    passed, warnings = check_value_bounds(db, species_id)
    if passed:
        print("   ✓ All values within expected bounds")
    else:
        for w in warnings:
            print(f"   ⚠️  {w}")
    
    # 4. Data coverage
    print("\n4. Data Coverage:")
    coverage = check_data_coverage(db, species_id)
    for col, pct in coverage.items():
        status = "✓" if pct > 50 else "⚠️"
        print(f"   {status} {col}: {pct:.1f}%")
    
    return all_passed


def main():
    """Run validation for all species."""
    print("="*60)
    print("Plant State 1Hz Validation")
    print("="*60)
    
    db = SessionLocal()
    
    try:
        # Get all species
        species_list = db.query(Species).all()
        
        if not species_list:
            print("No species found in database!")
            return
        
        all_passed = True
        for species in species_list:
            passed = validate_species(db, species.id, species.common_name)
            if not passed:
                all_passed = False
        
        print("\n" + "="*60)
        if all_passed:
            print("✓ ALL VALIDATIONS PASSED")
        else:
            print("✗ SOME VALIDATIONS FAILED - see details above")
        print("="*60)
        
        sys.exit(0 if all_passed else 1)
        
    finally:
        db.close()


if __name__ == "__main__":
    main()
