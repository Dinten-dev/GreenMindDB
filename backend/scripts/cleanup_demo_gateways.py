import logging
from app.database import SessionLocal
from app.models.master import Gateway

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def cleanup_demo_gateways():
    db = SessionLocal()
    try:
        gateways = db.query(Gateway).filter(
            Gateway.name.ilike('%demo%') | Gateway.hardware_id.ilike('%test%') | Gateway.hardware_id.ilike('%DEMO%')
        ).all()
        
        if not gateways:
            logger.info("No demo gateways found.")
            return

        for gw in gateways:
            logger.info(f"Deleting Demo Gateway: {gw.name} ({gw.hardware_id})")
            db.delete(gw)
        
        db.commit()
        logger.info(f"Successfully deleted {len(gateways)} demo gateways and their associated sensors.")
    except Exception as e:
        logger.error(f"Error cleaning up demo gateways: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    logger.info("Starting demo gateway cleanup...")
    cleanup_demo_gateways()
