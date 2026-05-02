#!/bin/bash
set -euo pipefail

# ─────────────────────────────────────────────────────────
# GreenMind — Unified Deploy Script
# Usage: ./scripts/deploy.sh --env staging|production
# ─────────────────────────────────────────────────────────

usage() {
    echo "Usage: $0 --env <staging|production>"
    echo ""
    echo "Options:"
    echo "  --env        Target environment (staging or production)"
    echo "  --skip-build Skip docker build (just restart services)"
    echo "  --help       Show this help"
    exit 1
}

# ── Defaults ─────────────────────────────────────────────
ENVIRONMENT=""
SKIP_BUILD=false

# ── Parse Args ───────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env) ENVIRONMENT="$2"; shift 2 ;;
        --skip-build) SKIP_BUILD=true; shift ;;
        --help) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

if [[ -z "$ENVIRONMENT" ]]; then
    echo "❌ --env is required"
    usage
fi

# ── Environment Config ───────────────────────────────────
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "$ENVIRONMENT" in
    staging)
        REMOTE_DIR="/home/traver/greenmind-staging"
        COMPOSE_FILE="docker-compose.staging.yml"
        COMPOSE_PROJECT="gm-staging"
        HEALTH_URL_FRONTEND="http://127.0.0.1:3001"
        HEALTH_URL_BACKEND="http://127.0.0.1:8001/health"
        LABEL="Staging (test.green-mind.ch)"
        ;;
    production)
        REMOTE_DIR="/home/traver/greenmind-prod"
        COMPOSE_FILE="docker-compose.prod.yml"
        COMPOSE_PROJECT="greenminddb"
        HEALTH_URL_FRONTEND="http://127.0.0.1:3000"
        HEALTH_URL_BACKEND="http://127.0.0.1:8000/health"
        LABEL="Production (green-mind.ch)"
        ;;
    *)
        echo "❌ Invalid environment: $ENVIRONMENT (must be staging or production)"
        exit 1
        ;;
esac

# ── SSH Config ───────────────────────────────────────────
# GitHub Actions sets DEPLOY_SSH_KEY, DEPLOY_HOST, DEPLOY_USER as env vars.
# Local usage falls back to dev-tools key and hardcoded values.
REMOTE_USER="${DEPLOY_USER:-traver}"
REMOTE_HOST="${DEPLOY_HOST:-188.245.247.156}"

if [[ -n "${DEPLOY_SSH_KEY_FILE:-}" ]]; then
    # CI: key file path provided directly
    SSH_KEY="$DEPLOY_SSH_KEY_FILE"
    SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes"
else
    # Local: use dev-tools key
    ORIG_SSH_KEY="${LOCAL_DIR}/dev-tools/greenmind_deploy_key"
    SSH_KEY="/tmp/greenmind_deploy_key_$$"
    cp "${ORIG_SSH_KEY}" "${SSH_KEY}"
    chmod 600 "${SSH_KEY}"
    trap "rm -f ${SSH_KEY}" EXIT
    SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=10"
fi

echo "🚀 Deploying GreenMind → ${LABEL}"
echo "   Target: ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}"
echo ""

# ── 1. Check SSH ─────────────────────────────────────────
echo "📡 Checking SSH connection..."
if ! ssh -q ${SSH_OPTS} -o BatchMode=yes "${REMOTE_USER}@${REMOTE_HOST}" exit; then
    echo "❌ SSH connection failed."
    exit 1
fi
echo "✅ SSH OK"

# ── 2. Ensure remote directory exists ────────────────────
ssh ${SSH_OPTS} "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p ${REMOTE_DIR}"

# ── 3. Sync Code ─────────────────────────────────────────
echo "🔄 Syncing code..."
rsync -az --delete \
    -e "ssh ${SSH_OPTS}" \
    --exclude 'node_modules' \
    --exclude '.git' \
    --exclude '.DS_Store' \
    --exclude 'venv' \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '.env' \
    --exclude 'postgres_data' \
    --exclude 'minio_data' \
    --exclude '.pytest_cache' \
    --exclude '.ruff_cache' \
    --exclude 'data' \
    "${LOCAL_DIR}/" \
    "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"
echo "✅ Code synced"

# ── 4. Check .env ────────────────────────────────────────
echo "📄 Checking .env on remote..."
if ssh ${SSH_OPTS} "${REMOTE_USER}@${REMOTE_HOST}" "test -f ${REMOTE_DIR}/.env"; then
    echo "✅ .env exists — not overwriting"
else
    echo "⚠️  No .env found. Copying example as template..."
    EXAMPLE_FILE=".env.${ENVIRONMENT}.example"
    if [[ -f "${LOCAL_DIR}/${EXAMPLE_FILE}" ]]; then
        scp ${SSH_OPTS} "${LOCAL_DIR}/${EXAMPLE_FILE}" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/.env"
    else
        scp ${SSH_OPTS} "${LOCAL_DIR}/.env.example" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/.env"
    fi
    echo "⚠️  IMPORTANT: SSH into the server and update ${REMOTE_DIR}/.env with real values!"
fi

# ── 5. Build & Deploy ────────────────────────────────────
echo "🐳 Building and starting containers..."
BUILD_FLAG=""
if [[ "$SKIP_BUILD" == "false" ]]; then
    BUILD_FLAG="--build"
fi

ssh ${SSH_OPTS} "${REMOTE_USER}@${REMOTE_HOST}" "
    export PATH=\$PATH:/usr/local/bin
    cd ${REMOTE_DIR}
    docker builder prune -f 2>/dev/null || true
    COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT} docker compose -f ${COMPOSE_FILE} build --no-cache
    COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT} docker compose -f ${COMPOSE_FILE} up -d --remove-orphans
"

# ── 6. Wait for healthy ─────────────────────────────────
echo "🏥 Waiting for services to become healthy..."
MAX_RETRIES=30
RETRY_INTERVAL=5
for i in $(seq 1 $MAX_RETRIES); do
    BACKEND_OK=$(ssh ${SSH_OPTS} "${REMOTE_USER}@${REMOTE_HOST}" \
        "curl -sf ${HEALTH_URL_BACKEND} > /dev/null 2>&1 && echo yes || echo no")
    FRONTEND_OK=$(ssh ${SSH_OPTS} "${REMOTE_USER}@${REMOTE_HOST}" \
        "curl -sf ${HEALTH_URL_FRONTEND} > /dev/null 2>&1 && echo yes || echo no")

    if [[ "$BACKEND_OK" == "yes" && "$FRONTEND_OK" == "yes" ]]; then
        echo "✅ All services healthy!"
        break
    fi

    if [[ $i -eq $MAX_RETRIES ]]; then
        echo "❌ Services did not become healthy within $((MAX_RETRIES * RETRY_INTERVAL))s"
        echo "   Backend: ${BACKEND_OK} | Frontend: ${FRONTEND_OK}"
        ssh ${SSH_OPTS} "${REMOTE_USER}@${REMOTE_HOST}" \
            "cd ${REMOTE_DIR} && COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT} docker compose -f ${COMPOSE_FILE} ps"
        exit 1
    fi

    echo "   Attempt ${i}/${MAX_RETRIES} — Backend: ${BACKEND_OK}, Frontend: ${FRONTEND_OK}"
    sleep $RETRY_INTERVAL
done

# ── 7. Show status ───────────────────────────────────────
echo ""
echo "📊 Container status:"
ssh ${SSH_OPTS} "${REMOTE_USER}@${REMOTE_HOST}" \
    "cd ${REMOTE_DIR} && COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT} docker compose -f ${COMPOSE_FILE} ps"

echo ""
echo "✅ Deployment to ${LABEL} completed successfully!"
