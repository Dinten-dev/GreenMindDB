from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.routers import species_router, metrics_router, sources_router, target_ranges_router, auth_router, telemetry
from app.routers.plant_state import router as plant_state_router, admin_router as resample_admin_router
from app.services.scheduler import start_scheduler, stop_scheduler

# Rate limiter
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan for startup/shutdown events."""
    # Startup
    start_scheduler()
    yield
    # Shutdown
    stop_scheduler()


app = FastAPI(
    title="Plant Wiki API",
    description="API for plant growing conditions database with authentication and ML data",
    version="2.1.0",
    lifespan=lifespan
)

# Add rate limiter to app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Set basic security headers for all API responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response

# Include routers
app.include_router(auth_router)
app.include_router(species_router)
app.include_router(metrics_router)
app.include_router(sources_router)
app.include_router(target_ranges_router)
app.include_router(telemetry.router)
app.include_router(telemetry.ingest_router)

# ML Data routers
app.include_router(plant_state_router)
app.include_router(resample_admin_router)


@app.get("/health")
def health_check():
    """Health check endpoint for Docker."""
    return {"status": "healthy"}


@app.get("/")
def root():
    """Root endpoint with API information."""
    return {
        "name": "Plant Wiki API",
        "version": "2.1.0",
        "docs": "/docs",
        "auth": {
            "signup": "/auth/signup",
            "login": "/auth/login"
        },
        "ml_data": {
            "timeseries": "/species/{id}/ml/timeseries",
            "latest": "/species/{id}/ml/latest",
            "download": "/species/{id}/ml/download"
        }
    }
