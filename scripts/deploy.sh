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
    echo ""
    echo "Required environment: DEPLOY_USER, DEPLOY_HOST, DEPLOY_SSH_KEY_FILE,"
    echo "DEPLOY_KNOWN_HOSTS_FILE. DEPLOY_REMOTE_BASE_DIR defaults to /home/DEPLOY_USER."
    exit 1
}

# ── Defaults ─────────────────────────────────────────────
ENVIRONMENT=""
SKIP_BUILD=false

# ── Parse Args ───────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)
            [[ $# -ge 2 ]] || { echo "❌ --env requires a value"; usage; }
            ENVIRONMENT="$2"
            shift 2
            ;;
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
        REMOTE_NAME="greenmind-staging"
        COMPOSE_FILE="docker-compose.staging.yml"
        COMPOSE_PROJECT="gm-staging"
        HEALTH_URL_FRONTEND="http://127.0.0.1:3001"
        HEALTH_URL_BACKEND="http://127.0.0.1:8001/health"
        LABEL="Staging (test.green-mind.ch)"
        ;;
    production)
        REMOTE_NAME="greenmind-prod"
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
# Deployment identity and host verification are explicit. The repository never
# supplies a private key and never accepts an unknown host key.
: "${DEPLOY_USER:?DEPLOY_USER is required}"
: "${DEPLOY_HOST:?DEPLOY_HOST is required}"
: "${DEPLOY_SSH_KEY_FILE:?DEPLOY_SSH_KEY_FILE is required}"
: "${DEPLOY_KNOWN_HOSTS_FILE:?DEPLOY_KNOWN_HOSTS_FILE is required}"

if [[ ! "$DEPLOY_USER" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "❌ DEPLOY_USER contains unsupported characters"
    exit 1
fi
if [[ ! "$DEPLOY_HOST" =~ ^[A-Za-z0-9._:-]+$ ]]; then
    echo "❌ DEPLOY_HOST contains unsupported characters"
    exit 1
fi

REMOTE_BASE="${DEPLOY_REMOTE_BASE_DIR:-/home/${DEPLOY_USER}}"
if [[ "$REMOTE_BASE" != /* || "$REMOTE_BASE" == *".."* || ! "$REMOTE_BASE" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    echo "❌ DEPLOY_REMOTE_BASE_DIR must be a simple absolute path without '..'"
    exit 1
fi
REMOTE_DIR="${REMOTE_BASE%/}/${REMOTE_NAME}"

REMOTE_USER="$DEPLOY_USER"
REMOTE_HOST="$DEPLOY_HOST"
SSH_KEY="$DEPLOY_SSH_KEY_FILE"
KNOWN_HOSTS="$DEPLOY_KNOWN_HOSTS_FILE"

if ! grep -q "PRIVATE KEY" "$SSH_KEY" 2>/dev/null; then
    echo "❌ DEPLOY_SSH_KEY does not contain a valid private key header. Please verify GitHub Secrets."
    exit 1
fi

SSH_OPTS=(
    -i "$SSH_KEY"
    -o "UserKnownHostsFile=$KNOWN_HOSTS"
    -o StrictHostKeyChecking=yes
    -o ConnectTimeout=10
    -o BatchMode=yes
)
printf -v RSYNC_SSH 'ssh -i %q -o UserKnownHostsFile=%q -o StrictHostKeyChecking=yes -o ConnectTimeout=10 -o BatchMode=yes' \
    "$SSH_KEY" "$KNOWN_HOSTS"

echo "🚀 Deploying GreenMind → ${LABEL}"
echo "   Target: ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}"
echo ""

# ── 1. Check SSH ─────────────────────────────────────────
echo "📡 Checking SSH connection..."
if ! ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" exit; then
    echo "❌ SSH connection to ${REMOTE_USER}@${REMOTE_HOST} failed."
    exit 1
fi
echo "✅ SSH OK"

# ── 2. Ensure remote directory exists ────────────────────
ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p ${REMOTE_DIR}"

# ── 3. Sync Code ─────────────────────────────────────────
echo "🔄 Syncing code..."
rsync -az --delete \
    -e "$RSYNC_SSH" \
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
    --exclude '.next' \
    --exclude 'keys' \
    "${LOCAL_DIR}/" \
    "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"
echo "✅ Code synced"

# ── 4. Check .env ────────────────────────────────────────
echo "📄 Checking .env on remote..."
if ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "test -f ${REMOTE_DIR}/.env"; then
    echo "✅ .env exists — not overwriting"
else
    echo "❌ ${REMOTE_DIR}/.env is missing. Provision server-side secrets before deployment."
    exit 1
fi

# Ensure release signing public key exists
ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "
    mkdir -p ${REMOTE_DIR}/keys 2>/dev/null || true
    if [ ! -f ${REMOTE_DIR}/keys/gateway-release-signing-public.pem ]; then
        touch ${REMOTE_DIR}/keys/gateway-release-signing-public.pem 2>/dev/null || true
    fi
"

# ── 5. Build & Deploy ────────────────────────────────────
echo "🐳 Building and starting containers..."
if [[ "$SKIP_BUILD" == "true" ]]; then
    COMPOSE_ACTION="COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT} docker compose -f ${COMPOSE_FILE} up -d --remove-orphans"
else
    COMPOSE_ACTION="COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT} docker compose -f ${COMPOSE_FILE} build && COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT} docker compose -f ${COMPOSE_FILE} up -d --remove-orphans"
fi

ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "
    set -e
    export PATH=\$PATH:/usr/local/bin
    cd ${REMOTE_DIR}
    ${COMPOSE_ACTION}
"

# ── 6. Sync Nginx config ────────────────────────────────
echo "🌐 Syncing Nginx config..."
if [[ "$ENVIRONMENT" == "production" ]]; then
    NGINX_SRC="${REMOTE_DIR}/nginx/green-mind.ch.conf"
    NGINX_DST="/etc/nginx/sites-available/greenmind"
elif [[ "$ENVIRONMENT" == "staging" ]]; then
    NGINX_SRC="${REMOTE_DIR}/nginx/test.green-mind.ch.conf"
    NGINX_DST="/etc/nginx/sites-available/greenmind-staging"
fi

ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "
    set -e
    if ! diff -q ${NGINX_SRC} ${NGINX_DST} > /dev/null 2>&1; then
        echo '⚠️ Nginx config changed. Attempting to sync...'
        if sudo -n true 2>/dev/null; then
            sudo cp ${NGINX_SRC} ${NGINX_DST}
            sudo nginx -t && sudo systemctl reload nginx
            echo '✅ Nginx config updated and reloaded'
        else
            echo '⚠️ Passwordless sudo is not available for automatic Nginx config sync.'
            echo 'Containers will deploy, but please SSH into the server to update Nginx if needed:'
            echo '   sudo cp ${NGINX_SRC} ${NGINX_DST}'
            echo '   sudo nginx -t && sudo systemctl reload nginx'
        fi
    else
        echo '✅ Nginx config unchanged — skipping reload'
    fi
"

# ── 7. Wait for healthy ─────────────────────────────────
echo "🏥 Waiting for services to become healthy..."
MAX_RETRIES=30
RETRY_INTERVAL=5
for i in $(seq 1 $MAX_RETRIES); do
    BACKEND_OK=$(ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" \
        "curl -sf --connect-timeout 5 --max-time 10 ${HEALTH_URL_BACKEND} > /dev/null 2>&1 && echo yes || echo no")
    FRONTEND_OK=$(ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" \
        "curl -sf --connect-timeout 5 --max-time 10 ${HEALTH_URL_FRONTEND} > /dev/null 2>&1 && echo yes || echo no")

    if [[ "$BACKEND_OK" == "yes" && "$FRONTEND_OK" == "yes" ]]; then
        echo "✅ All services healthy!"
        break
    fi

    if [[ $i -eq $MAX_RETRIES ]]; then
        echo "❌ Services did not become healthy within $((MAX_RETRIES * RETRY_INTERVAL))s"
        echo "   Backend: ${BACKEND_OK} | Frontend: ${FRONTEND_OK}"
        ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" \
            "cd ${REMOTE_DIR} && COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT} docker compose -f ${COMPOSE_FILE} ps"
        exit 1
    fi

    echo "   Attempt ${i}/${MAX_RETRIES} — Backend: ${BACKEND_OK}, Frontend: ${FRONTEND_OK}"
    sleep $RETRY_INTERVAL
done

# ── 8. Show status ───────────────────────────────────────
echo ""
echo "📊 Container status:"
ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" \
    "cd ${REMOTE_DIR} && COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT} docker compose -f ${COMPOSE_FILE} ps"

echo ""
echo "✅ Deployment to ${LABEL} completed successfully!"
