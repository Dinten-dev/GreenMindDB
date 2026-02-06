"""
Seed data script for Plant Wiki database.
Populates species, metrics, sources, and target ranges.
Simplified to exactly 5 metrics per plant.
"""
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Species, Metric, Source, TargetRange
from app.config import settings


def seed_database():
    """Seed the database with initial plant data."""
    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # Check if already seeded
    if session.query(Species).first():
        print("Database already seeded, skipping...")
        session.close()
        return
    
    print("Seeding database...")
    
    # === METRICS (exactly 5 required metrics) ===
    metrics = {
        "air_temperature_c": Metric(
            key="air_temperature_c",
            label="Air Temperature",
            unit="°C",
            description="Optimal air temperature range for plant growth"
        ),
        "rel_humidity_pct": Metric(
            key="rel_humidity_pct",
            label="Relative Humidity",
            unit="%",
            description="Relative humidity percentage in growing environment"
        ),
        "soil_moisture_vwc_pct": Metric(
            key="soil_moisture_vwc_pct",
            label="Soil Moisture (VWC)",
            unit="%",
            description="Volumetric Water Content in soil"
        ),
        "light_ppfd_umol_m2_s": Metric(
            key="light_ppfd_umol_m2_s",
            label="Light Intensity (PPFD)",
            unit="μmol/m²/s",
            description="Photosynthetic Photon Flux Density"
        ),
        "soil_ph": Metric(
            key="soil_ph",
            label="Soil pH",
            unit="pH",
            description="Soil acidity/alkalinity level"
        )
    }
    session.add_all(metrics.values())
    session.flush()
    
    # === SOURCES ===
    sources = {
        "usu": Source(
            title="Vegetable Growing Guide",
            publisher="Utah State University Extension",
            year=2023,
            url="https://extension.usu.edu/yardandgarden/vegetables",
            notes="Comprehensive vegetable cultivation guide"
        ),
        "clemson": Source(
            title="Home & Garden Information Center",
            publisher="Clemson University Cooperative Extension",
            year=2022,
            url="https://hgic.clemson.edu/",
            notes="Extension vegetable guides"
        ),
        "osu": Source(
            title="Greenhouse Production Guides",
            publisher="Oregon State University Extension",
            year=2021,
            url="https://catalog.extension.oregonstate.edu/",
            notes="Commercial greenhouse production"
        ),
        "cornell": Source(
            title="Home Gardening Guide",
            publisher="Cornell University",
            year=2022,
            url="https://gardening.cornell.edu/",
            notes="Soil and growing requirements"
        ),
        "tamu": Source(
            title="Vegetable Production Guide",
            publisher="Texas A&M AgriLife Extension",
            year=2021,
            url="https://aggie-horticulture.tamu.edu/",
            notes="Commercial production guidelines"
        )
    }
    session.add_all(sources.values())
    session.flush()
    
    # === SPECIES with exactly 5 target_range entries each ===
    
    # LETTUCE
    lettuce = Species(
        common_name="Lettuce",
        latin_name="Lactuca sativa",
        category="Leafy Green",
        notes="Cool-season crop. Best grown in spring/fall. Bolts in high temperatures."
    )
    session.add(lettuce)
    session.flush()
    
    lettuce_targets = [
        TargetRange(species_id=lettuce.id, metric_id=metrics["air_temperature_c"].id,
                    optimal_low=15, optimal_high=21, source_id=sources["usu"].id),
        TargetRange(species_id=lettuce.id, metric_id=metrics["rel_humidity_pct"].id,
                    optimal_low=50, optimal_high=70, source_id=sources["usu"].id),
        TargetRange(species_id=lettuce.id, metric_id=metrics["soil_moisture_vwc_pct"].id,
                    optimal_low=40, optimal_high=60, source_id=sources["clemson"].id),
        TargetRange(species_id=lettuce.id, metric_id=metrics["light_ppfd_umol_m2_s"].id,
                    optimal_low=250, optimal_high=400, source_id=sources["osu"].id),
        TargetRange(species_id=lettuce.id, metric_id=metrics["soil_ph"].id,
                    optimal_low=6.0, optimal_high=6.5, source_id=sources["cornell"].id),
    ]
    session.add_all(lettuce_targets)
    
    # TOMATO
    tomato = Species(
        common_name="Tomato",
        latin_name="Solanum lycopersicum",
        category="Fruit",
        notes="Warm-season fruiting crop. Requires consistent temperatures and high light."
    )
    session.add(tomato)
    session.flush()
    
    tomato_targets = [
        TargetRange(species_id=tomato.id, metric_id=metrics["air_temperature_c"].id,
                    optimal_low=22, optimal_high=26, source_id=sources["osu"].id),
        TargetRange(species_id=tomato.id, metric_id=metrics["rel_humidity_pct"].id,
                    optimal_low=60, optimal_high=70, source_id=sources["osu"].id),
        TargetRange(species_id=tomato.id, metric_id=metrics["soil_moisture_vwc_pct"].id,
                    optimal_low=30, optimal_high=40, source_id=sources["tamu"].id),
        TargetRange(species_id=tomato.id, metric_id=metrics["light_ppfd_umol_m2_s"].id,
                    optimal_low=400, optimal_high=700, source_id=sources["osu"].id),
        TargetRange(species_id=tomato.id, metric_id=metrics["soil_ph"].id,
                    optimal_low=5.8, optimal_high=6.5, source_id=sources["cornell"].id),
    ]
    session.add_all(tomato_targets)
    
    # CUCUMBER
    cucumber = Species(
        common_name="Cucumber",
        latin_name="Cucumis sativus",
        category="Fruit",
        notes="Warm-season crop. High humidity tolerance. Fast growing with high light needs."
    )
    session.add(cucumber)
    session.flush()
    
    cucumber_targets = [
        TargetRange(species_id=cucumber.id, metric_id=metrics["air_temperature_c"].id,
                    optimal_low=23, optimal_high=27, source_id=sources["osu"].id),
        TargetRange(species_id=cucumber.id, metric_id=metrics["rel_humidity_pct"].id,
                    optimal_low=60, optimal_high=75, source_id=sources["osu"].id),
        TargetRange(species_id=cucumber.id, metric_id=metrics["soil_moisture_vwc_pct"].id,
                    optimal_low=50, optimal_high=70, source_id=sources["tamu"].id),
        TargetRange(species_id=cucumber.id, metric_id=metrics["light_ppfd_umol_m2_s"].id,
                    optimal_low=400, optimal_high=600, source_id=sources["osu"].id),
        TargetRange(species_id=cucumber.id, metric_id=metrics["soil_ph"].id,
                    optimal_low=6.0, optimal_high=6.5, source_id=sources["cornell"].id),
    ]
    session.add_all(cucumber_targets)
    
    # BASIL
    basil = Species(
        common_name="Basil",
        latin_name="Ocimum basilicum",
        category="Herb",
        notes="Warm-season herb. Sensitive to cold. Popular for hydroponic cultivation."
    )
    session.add(basil)
    session.flush()
    
    basil_targets = [
        TargetRange(species_id=basil.id, metric_id=metrics["air_temperature_c"].id,
                    optimal_low=21, optimal_high=27, source_id=sources["tamu"].id),
        TargetRange(species_id=basil.id, metric_id=metrics["rel_humidity_pct"].id,
                    optimal_low=50, optimal_high=60, source_id=sources["tamu"].id),
        TargetRange(species_id=basil.id, metric_id=metrics["soil_moisture_vwc_pct"].id,
                    optimal_low=40, optimal_high=60, source_id=sources["cornell"].id),
        TargetRange(species_id=basil.id, metric_id=metrics["light_ppfd_umol_m2_s"].id,
                    optimal_low=300, optimal_high=500, source_id=sources["osu"].id),
        TargetRange(species_id=basil.id, metric_id=metrics["soil_ph"].id,
                    optimal_low=5.8, optimal_high=6.5, source_id=sources["cornell"].id),
    ]
    session.add_all(basil_targets)
    
    # RADISH
    radish = Species(
        common_name="Radish",
        latin_name="Raphanus sativus",
        category="Root Vegetable",
        notes="Fast-growing cool-season crop. Ready in 25-30 days. Bolts in heat."
    )
    session.add(radish)
    session.flush()
    
    radish_targets = [
        TargetRange(species_id=radish.id, metric_id=metrics["air_temperature_c"].id,
                    optimal_low=15, optimal_high=18, source_id=sources["usu"].id),
        TargetRange(species_id=radish.id, metric_id=metrics["rel_humidity_pct"].id,
                    optimal_low=50, optimal_high=70, source_id=sources["usu"].id),
        TargetRange(species_id=radish.id, metric_id=metrics["soil_moisture_vwc_pct"].id,
                    optimal_low=50, optimal_high=70, source_id=sources["tamu"].id),
        TargetRange(species_id=radish.id, metric_id=metrics["light_ppfd_umol_m2_s"].id,
                    optimal_low=250, optimal_high=400, source_id=sources["tamu"].id),
        TargetRange(species_id=radish.id, metric_id=metrics["soil_ph"].id,
                    optimal_low=6.0, optimal_high=7.0, source_id=sources["cornell"].id),
    ]
    session.add_all(radish_targets)
    
    session.commit()
    print(f"✓ Seeded {session.query(Species).count()} species")
    print(f"✓ Seeded {session.query(Metric).count()} metrics")
    print(f"✓ Seeded {session.query(Source).count()} sources")
    print(f"✓ Seeded {session.query(TargetRange).count()} target ranges (5 per species)")
    print("Database seeding complete!")
    
    session.close()


if __name__ == "__main__":
    seed_database()
