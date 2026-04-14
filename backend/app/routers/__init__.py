from app.routers.auth import router as auth_router
from app.routers.biosignal import router as biosignal_router
from app.routers.contact import router as contact_router
from app.routers.gateways import router as gateways_router
from app.routers.ingest import router as ingest_router
from app.routers.organizations import router as organizations_router
from app.routers.sensors import router as sensors_router
from app.routers.wav import router as wav_router
from app.routers.ws import router as ws_router
from app.routers.zones import router as zones_router
from app.routers.firmware import router as firmware_router
from app.routers.gateway_desired_state import router as gateway_desired_state_router
from app.routers.gateway_admin import router as gateway_admin_router

__all__ = [
    "auth_router",
    "contact_router",
    "gateways_router",
    "ingest_router",
    "organizations_router",
    "sensors_router",
    "wav_router",
    "ws_router",
    "zones_router",
    "biosignal_router",
    "firmware_router",
    "gateway_desired_state_router",
    "gateway_admin_router",
]
