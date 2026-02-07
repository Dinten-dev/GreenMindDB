FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create entrypoint script for migrations + data import
RUN echo '#!/bin/bash\n\
    set -e\n\
    echo "Running migrations..."\n\
    alembic upgrade head\n\
    echo "Seeding database..."\n\
    python -m app.seed.seed_data || true\n\
    echo "Importing WAV data if available..."\n\
    if [ -d "/data/wav" ] && ls /data/wav/*.wav 1> /dev/null 2>&1; then\n\
    python /app/scripts/import_wav_data.py || echo "WAV import skipped"\n\
    fi\n\
    echo "Starting server..."\n\
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000\n\
    ' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
