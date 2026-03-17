"""GreenMind API – FastAPI application."""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.routers import (
    auth_router,
    organizations_router,
    greenhouses_router,
    devices_router,
    sensors_router,
    ingest_router,
    contact_router,
)

app = FastAPI(
    title="GreenMind API",
    description="Plant bioelectrical sensing platform for predictive yield optimization",
    version="3.0.0",
)

# CORS – allow credentials for httpOnly cookie auth
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Api-Key"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# Rate limiter for contact endpoints
from app.routers.contact import limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Routers
app.include_router(auth_router)
app.include_router(organizations_router)
app.include_router(greenhouses_router)
app.include_router(devices_router)
app.include_router(sensors_router)
app.include_router(ingest_router)
app.include_router(contact_router)


@app.get("/health")
def health_check():
    """Health check endpoint for Docker."""
    return {"status": "healthy"}


@app.get("/")
def root():
    return {
        "name": "GreenMind API",
        "version": "3.0.0",
        "docs": "/docs",
    }
