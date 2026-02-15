"""GreenMindDB Mac mini API – app entry point."""
import logging
import time

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text

from app.macmini.config import get_settings
from app.macmini.database import get_db, engine
from app.macmini.schemas import HealthResponse
from app.macmini.storage import get_s3_client

settings = get_settings()
logger = logging.getLogger("greenmind.macmini")
logging.basicConfig(
    level=settings.log_level.upper(),
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
)

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="GreenMindDB Mac mini API",
    version="2.0.0",
    description="Greenhouse monitoring platform – ingestion, dashboard, ground-truth, annotations, ML export.",
)

# CORS
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Routers ───────────────────────────────────────────────────
from app.macmini.routers.auth import router as auth_router        # noqa: E402
from app.macmini.routers.admin import router as admin_router      # noqa: E402
from app.macmini.routers.operator import router as operator_router  # noqa: E402
from app.macmini.routers.ingest import router as ingest_router    # noqa: E402
from app.macmini.routers.media import router as media_router      # noqa: E402
from app.macmini.routers.export import router as export_router    # noqa: E402

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(operator_router)
app.include_router(ingest_router)
app.include_router(media_router)
app.include_router(export_router)


# ── Structured logging middleware ─────────────────────────────
@app.middleware("http")
async def structured_logging_middleware(request: Request, call_next):
    start = time.monotonic()
    response: Response = await call_next(request)
    duration = time.monotonic() - start
    logger.info(
        "request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round(duration * 1000, 1),
            "ip": request.client.host if request.client else None,
        },
    )
    return response


# ── Startup: seed admin user ─────────────────────────────────
@app.on_event("startup")
def seed_admin_user():
    """Create default admin user if none exists."""
    from app.macmini.auth import hash_password
    from app.models import User, Role
    from app.macmini.database import SessionLocal

    db = SessionLocal()
    try:
        if not db.query(User).filter(User.role == Role.ADMIN).first():
            admin = User(
                email=settings.admin_email,
                password_hash=hash_password(settings.admin_password),
                role=Role.ADMIN,
                greenhouse_id=None,
            )
            db.add(admin)
            db.commit()
            logger.info(f"Seeded admin user: {settings.admin_email}")
    except Exception:
        db.rollback()
        logger.exception("Failed to seed admin user")
    finally:
        db.close()


@app.on_event("startup")
def ensure_s3_bucket():
    """Create the S3 bucket if it does not exist."""
    try:
        client = get_s3_client()
        try:
            client.head_bucket(Bucket=settings.s3_bucket)
        except Exception:
            client.create_bucket(Bucket=settings.s3_bucket)
            logger.info(f"Created S3 bucket: {settings.s3_bucket}")
    except Exception:
        logger.warning("Could not ensure S3 bucket (MinIO may be unavailable)")


# ── Prometheus metrics ────────────────────────────────────────
Instrumentator().instrument(app).expose(app, include_in_schema=False, should_gzip=True)


# ── Health check ──────────────────────────────────────────────
@app.get("/health", response_model=HealthResponse)
def health():
    db_ok = True
    minio_ok = True

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    try:
        client = get_s3_client()
        client.head_bucket(Bucket=settings.s3_bucket)
    except Exception:
        minio_ok = False

    overall = "ok" if db_ok and minio_ok else "degraded"
    return HealthResponse(
        status=overall,
        db="ok" if db_ok else "error",
        minio="ok" if minio_ok else "error",
    )

