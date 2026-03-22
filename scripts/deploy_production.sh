#!/bin/bash
set -e

# Configuration for the Production Server
REMOTE_USER="traver"
REMOTE_HOST="188.245.247.156"
REMOTE_DIR="/home/traver/GreenMindDB"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Deploy Key Setup (passwortfreier Key aus dem Projekt)
ORIG_SSH_KEY="${LOCAL_DIR}/dev-tools/greenmind_deploy_key"
SSH_KEY="/tmp/greenmind_deploy_key_prod"

# Copy key to temp location to ensure correct permissions
cp "${ORIG_SSH_KEY}" "${SSH_KEY}"
chmod 600 "${SSH_KEY}"
trap "rm -f ${SSH_KEY}" EXIT

SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o ConnectTimeout=5"

echo "🚀 Deploying GreenMindDB to ${REMOTE_USER}@${REMOTE_HOST}..."

# 1. Check SSH connection
echo "📡 Checking SSH connection..."
if ! ssh -q ${SSH_OPTS} -o BatchMode=yes "${REMOTE_USER}@${REMOTE_HOST}" exit; then
    echo "❌ SSH connection failed. Bitte kopiere zuerst den Key zum Server mit:"
    echo "ssh-copy-id -i dev-tools/greenmind_deploy_key ${REMOTE_USER}@${REMOTE_HOST}"
    exit 1
fi
echo "✅ SSH connection successful."

# 2. Sync Code using Rsync
echo "🔄 Syncing code to remote server..."
rsync -avz --delete \
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
    "${LOCAL_DIR}/" \
    "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"

# 3. .env prüfen/kopieren
echo "📄 Checking .env on remote..."
ssh ${SSH_OPTS} "${REMOTE_USER}@${REMOTE_HOST}" "if [ ! -f ${REMOTE_DIR}/.env ]; then echo '⚠️ No .env found on remote. Copying local .env...'; fi"
scp ${SSH_OPTS} "${LOCAL_DIR}/.env" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/.env"

# 4. Deploy with Docker Compose auf dem Server
echo "🐳 Building and starting Docker containers on remote server..."
ssh ${SSH_OPTS} "${REMOTE_USER}@${REMOTE_HOST}" "export PATH=\$PATH:/usr/local/bin && cd ${REMOTE_DIR} && docker compose -f docker-compose.prod.yml up -d --build --remove-orphans"

# 5. Status prüfen
echo "🏥 Checking service status..."
ssh ${SSH_OPTS} "${REMOTE_USER}@${REMOTE_HOST}" "export PATH=\$PATH:/usr/local/bin && cd ${REMOTE_DIR} && docker compose -f docker-compose.prod.yml ps"

echo "✅ Deployment completed successfully!"
echo "🌍 Frontend should be available at http://${REMOTE_HOST}:3000"
echo "🔧 Backend Docs at http://${REMOTE_HOST}:8000/docs"
