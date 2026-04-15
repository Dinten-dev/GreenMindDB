#!/bin/bash
set -eo pipefail

# 1. Load Environment Variables (if .env exists)
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# 2. Ensure Local Storage
# We get the path from the script output or define it manually if needed.
# For now, let's hardcode the expectation or capture it.
# Actually, ensure_local_storage.sh exports LOCAL_DATA_ROOT if sourced.
source ./scripts/ensure_local_storage.sh

# 3. Source Docker Environment (UID/GID)
if [ -f ./dev-tools/docker-env.sh ]; then
    source ./dev-tools/docker-env.sh
else
    export CURRENT_UID=$(id -u)
    export CURRENT_GID=$(id -g)
fi

echo "=== deploying locally ==="
echo "User: $CURRENT_UID:$CURRENT_GID"
echo "Volumes: $LOCAL_DATA_ROOT"

# Ensure clean export
export PGDATA_DIR="$LOCAL_DATA_ROOT/postgres"
export MINIO_DATA_DIR="$LOCAL_DATA_ROOT/minio"
export PG_PORT=5432
export MINIO_PORT=9000
export MINIO_CONSOLE_PORT=9001

# 4. Start Docker Compose
# We pass the env vars explicitly to be sure
# Note: Variables are already exported above.
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --remove-orphans

# 5. Check Status
echo "Waiting for services..."
sleep 5
docker compose ps

echo "🚀 Deployment Complete."
echo "Data stored in: $LOCAL_DATA_ROOT"
