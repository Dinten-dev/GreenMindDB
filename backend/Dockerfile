FROM python:3.11.11-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy application code
COPY . .

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

# Run migrations and start API (production-like worker model)
CMD ["sh", "-c", "alembic upgrade head && gunicorn app.macmini.main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:${API_PORT:-8000} --workers ${GUNICORN_WORKERS:-2}"]
