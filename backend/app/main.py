from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.routers import species_router, metrics_router, sources_router, target_ranges_router, auth_router, telemetry

# Rate limiter
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Plant Wiki API",
    description="API for plant growing conditions database with authentication",
    version="2.0.0"
)

# Add rate limiter to app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth_router)
app.include_router(species_router)
app.include_router(metrics_router)
app.include_router(sources_router)
app.include_router(target_ranges_router)
app.include_router(telemetry.router)
app.include_router(telemetry.ingest_router)


@app.get("/health")
def health_check():
    """Health check endpoint for Docker."""
    return {"status": "healthy"}


@app.get("/")
def root():
    """Root endpoint with API information."""
    return {
        "name": "Plant Wiki API",
        "version": "2.0.0",
        "docs": "/docs",
        "auth": {
            "signup": "/auth/signup",
            "login": "/auth/login"
        }
    }
