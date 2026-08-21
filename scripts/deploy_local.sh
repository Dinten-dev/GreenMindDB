#!/usr/bin/env bash
set -Eeuo pipefail

readonly script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly repository_dir="$(cd -- "${script_dir}/.." && pwd)"
cd -- "${repository_dir}"

# Docker Compose loads .env itself. Do not source it as shell code. For local
# storage, accept only the literal value of LOCAL_DATA_ROOT or use a safe default.
if [[ -z "${LOCAL_DATA_ROOT:-}" && -f .env ]]; then
    LOCAL_DATA_ROOT="$(sed -n 's/^LOCAL_DATA_ROOT=//p' .env | tail -n 1)"
fi
export LOCAL_DATA_ROOT="${LOCAL_DATA_ROOT:-${HOME}/LocalData/greenmind}"
source "${script_dir}/ensure_local_storage.sh"

export CURRENT_UID="$(id -u)"
export CURRENT_GID="$(id -g)"

echo "=== deploying locally ==="
echo "Frontend bind-mount user: $CURRENT_UID:$CURRENT_GID"
echo "Volumes: $LOCAL_DATA_ROOT"

# Ensure clean export
export PGDATA_DIR="$LOCAL_DATA_ROOT/postgres"
export MINIO_DATA_DIR="$LOCAL_DATA_ROOT/minio"
export POSTGRES_PORT=5432
export MINIO_PORT=9000
export MINIO_CONSOLE_PORT=9001

docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --remove-orphans

echo "Waiting for services..."
sleep 5
docker compose ps

echo "🚀 Deployment Complete."
echo "Data stored in: $LOCAL_DATA_ROOT"
