"""GreenMind API – FastAPI application."""

import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.logging_config import get_logger, setup_logging

# ── Initialize structured logging ────────────────────────────────────
setup_logging(settings.log_level)
logger = get_logger(__name__)

# ── Router imports ───────────────────────────────────────────────────
from app.routers import (  # noqa: E402
    auth_router,
    contact_router,
    devices_router,
    greenhouses_router,
    ingest_router,
    organizations_router,
    sensors_router,
)
from app.routers.contact import limiter  # noqa: E402

# ── App initialization ───────────────────────────────────────────────
app = FastAPI(
    title="GreenMind API",
    description="Plant bioelectrical sensing platform for predictive yield optimization",
    version="3.0.0",
)

# ── CORS ─────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Api-Key"],
)


# ── Middleware ───────────────────────────────────────────────────────


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Append hardened security headers to every response."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
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
            request.url.path,
            response.status_code,
            extra={"duration_ms": f"{duration_ms:.1f}"},
        )
    return response


# ── Rate limiter ─────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Routers ──────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(organizations_router)
app.include_router(greenhouses_router)
app.include_router(devices_router)
app.include_router(sensors_router)
app.include_router(ingest_router)
app.include_router(contact_router)


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
        "docs": "/docs",
    }


logger.info("GreenMind API initialized", extra={"version": "3.0.0"})
