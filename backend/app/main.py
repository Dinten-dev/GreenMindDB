"""GreenMind API – FastAPI application."""

import asyncio
import re
import time
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import REGISTRY
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.logging_config import get_logger, setup_logging
from app.operational_metrics import OperationalCollector

# ── Initialize structured logging ────────────────────────────────────
setup_logging(settings.log_level)
logger = get_logger(__name__)

_PUBLIC_SESSION_PATH = re.compile(r"(/api/v1/public/(?:observe|evaluate)/session/)[^/]+")


def _redact_sensitive_path(path: str) -> str:
    """Remove bearer-like public session tokens before request logging."""
    return _PUBLIC_SESSION_PATH.sub(r"\1[REDACTED]", path)


def _content_security_policy(*, production: bool) -> str:
    """Return a strict API policy, retaining Swagger compatibility in development."""
    script_sources = "'self'" if production else "'self' 'unsafe-inline' 'unsafe-eval'"
    style_sources = "'self'" if production else "'self' 'unsafe-inline'"
    return (
        "default-src 'self'; "
        f"script-src {script_sources}; "
        f"style-src {style_sources}; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'self' ws: wss:; "
        "frame-ancestors 'none'"
    )


@asynccontextmanager
async def _lifespan(application: FastAPI):
    tasks = []
    if settings.embedded_background_workers_enabled and settings.wav_feature_extraction_enabled:
        feature_task = asyncio.create_task(_wav_feature_loop())
        application.state.wav_feature_task = feature_task
        tasks.append(feature_task)
        logger.info("Scheduled WAV feature extraction enabled")
    if settings.embedded_background_workers_enabled and settings.retention_enabled:
        retention_task = asyncio.create_task(_retention_loop())
        application.state.retention_task = retention_task
        tasks.append(retention_task)
        logger.info("Scheduled retention enabled; dry_run=%s", settings.retention_dry_run)
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


# ── Router imports ───────────────────────────────────────────────────


from app.rate_limit import limiter  # noqa: E402
from app.routers import (  # noqa: E402
    auth_router,
    contact_router,
    firmware_router,
    gateway_admin_router,
    gateway_desired_state_router,
    gateways_router,
    ingest_router,
    organizations_router,
    plants_router,
    public_evaluate_router,
    public_observe_router,
    sensors_router,
    wav_router,
    ws_router,
    zones_router,
)

# ── App initialization ───────────────────────────────────────────────

_is_production = settings.environment.lower() in {"prod", "production"}

app = FastAPI(
    title="GreenMind API",
    description="R&D platform for bioelectrical plant signal analysis — Galaxyadvisors AG",
    version="3.0.0",
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
    lifespan=_lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Api-Key"],
)


# ── Monitoring ───────────────────────────────────────────────────────
# /metrics is safe: backend port is bound to 127.0.0.1 in production,
# only reachable from the Docker network (Prometheus) and localhost.
Instrumentator().instrument(app).expose(app, endpoint="/metrics")
REGISTRY.register(OperationalCollector())

# ── Middleware ───────────────────────────────────────────────────────


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Append hardened security headers to every response."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = _content_security_policy(
        production=_is_production
    )
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if _is_production:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every request with method, path, status, and duration."""
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    # Skip noisy health-check logging
    if request.url.path != "/health":
        logger.info(
            "%s %s → %s",
            request.method,
            _redact_sensitive_path(request.url.path),
            response.status_code,
            extra={"duration_ms": f"{duration_ms:.1f}"},
        )
    return response


# ── Rate limiter ─────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Routers ──────────────────────────────────────────────────────────
api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(auth_router)
api_v1_router.include_router(organizations_router)
api_v1_router.include_router(zones_router)
api_v1_router.include_router(gateways_router)
api_v1_router.include_router(sensors_router)
api_v1_router.include_router(ingest_router)
api_v1_router.include_router(contact_router)
api_v1_router.include_router(wav_router)
api_v1_router.include_router(ws_router)
api_v1_router.include_router(firmware_router)
api_v1_router.include_router(gateway_desired_state_router)
api_v1_router.include_router(gateway_admin_router)
api_v1_router.include_router(plants_router)
api_v1_router.include_router(public_observe_router)
api_v1_router.include_router(public_evaluate_router)

if settings.enable_experimental_biosignal:
    from app.routers.biosignal import router as biosignal_router

    logger.warning("Experimental biosignal API is enabled")
    api_v1_router.include_router(biosignal_router)

if settings.enable_experimental_provisioning:
    from app.routers.provisioning import router as provisioning_router

    logger.warning("Experimental provisioning API is enabled")
    api_v1_router.include_router(provisioning_router)

app.include_router(api_v1_router)


async def _wav_feature_loop() -> None:
    """Extract verified features independently from destructive retention."""
    from app.database import SessionLocal
    from app.services.wav_feature_service import process_pending_wav_features

    while True:
        db = SessionLocal()
        try:
            result = await asyncio.to_thread(process_pending_wav_features, db)
            if result["verified"] or result["failed"]:
                logger.info("Scheduled WAV feature extraction completed", extra=result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled WAV feature extraction failed")
        finally:
            db.close()
        await asyncio.sleep(settings.wav_feature_interval_minutes * 60)


async def _retention_loop() -> None:
    """Run retention periodically without blocking API request handling."""
    from app.database import SessionLocal
    from app.services.retention_service import run_retention

    while True:
        db = SessionLocal()
        try:
            result = await asyncio.to_thread(run_retention, db)
            logger.info("Scheduled retention completed", extra=result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled retention failed")
        finally:
            db.close()
        await asyncio.sleep(settings.retention_interval_hours * 3600)


# ── Health & Root ────────────────────────────────────────────────────


@app.get("/health")
def health_check():
    """Health check endpoint for Docker / load balancer."""
    return {"status": "healthy"}


@app.get("/")
def root():
    """API root – basic info."""
    return {
        "name": "GreenMind API",
        "version": "3.0.0",
        "docs": "/docs" if not _is_production else None,
    }


logger.info("GreenMind API initialized", extra={"version": "3.0.0"})
