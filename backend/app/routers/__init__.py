from app.routers.species import router as species_router
from app.routers.metrics import router as metrics_router
from app.routers.sources import router as sources_router
from app.routers.target_ranges import router as target_ranges_router
from app.routers.auth import router as auth_router

__all__ = ["species_router", "metrics_router", "sources_router", "target_ranges_router", "auth_router"]
