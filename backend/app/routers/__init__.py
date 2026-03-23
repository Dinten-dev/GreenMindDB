from app.routers.auth import router as auth_router
from app.routers.contact import router as contact_router
from app.routers.devices import router as devices_router
from app.routers.greenhouses import router as greenhouses_router
from app.routers.ingest import router as ingest_router
from app.routers.organizations import router as organizations_router
from app.routers.sensors import router as sensors_router
from app.routers.ws import router as ws_router

__all__ = [
    "auth_router",
    "organizations_router",
    "greenhouses_router",
    "devices_router",
    "sensors_router",
    "ingest_router",
    "contact_router",
    "ws_router",
]
