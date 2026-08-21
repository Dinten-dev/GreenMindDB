FROM python:3.12.13-slim-bookworm AS base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install the fully resolved production graph
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --requirement requirements.txt

# Copy application code
COPY . .

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/firmware_data/gateway_releases \
    && chown -R appuser:appuser /app

FROM base AS test

RUN python -m pip install --no-cache-dir --requirement requirements-dev.txt

USER appuser

CMD ["python", "-m", "pytest", "tests/", "-v"]

FROM base AS production

USER appuser

# Run migrations and start API
CMD ["sh", "-c", "alembic upgrade head && gunicorn app.main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-2}"]
