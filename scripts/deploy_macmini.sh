#!/bin/bash
set -e

# Configuration
REMOTE_USER="traverdinten"
REMOTE_HOST="192.168.1.100"
REMOTE_DIR="~/GreenMindDB"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORIG_SSH_KEY="${LOCAL_DIR}/dev-tools/greenmind_deploy_key"
SSH_KEY="/tmp/greenmind_deploy_key"

# Copy key to temp location to avoid space issues in path
cp "${ORIG_SSH_KEY}" "${SSH_KEY}"
chmod 600 "${SSH_KEY}"

# Cleanup temp key on exit
trap "rm -f ${SSH_KEY}" EXIT

SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no"

echo "🚀 Deploying GreenMindDB to ${REMOTE_USER}@${REMOTE_HOST}..."

# 1. Check SSH connection
echo "📡 Checking SSH connection..."

if ! ssh -q ${SSH_OPTS} -o BatchMode=yes -o ConnectTimeout=5 "${REMOTE_USER}@${REMOTE_HOST}" exit; then
    echo "❌ SSH connection failed. Please ensure you have set up the deploy key."
    exit 1
fi
echo "✅ SSH connection successful."

# 2. Sync Code
echo "🔄 Syncing code to remote server..."
# Using rsync to exclude heavy folders and git metadata
rsync -avz --delete \
    -e "ssh ${SSH_OPTS}" \
    --exclude 'venv' \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude 'node_modules' \
    --exclude '.git' \
    --exclude '.DS_Store' \
    --exclude '.env' \
    --exclude 'postgres_data' \
    --exclude 'minio_data' \
    "${LOCAL_DIR}/" \
    "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"

# 3. Sync .env file (Ask user or copy strictly if safe - for now we copy .env but warn)
# Ideally, .env should be managed separately on the server.
# For this setup, we will check if .env exists on remote, if not we copy .env.example or local .env
echo "📄 Checking .env on remote..."
ssh ${SSH_OPTS} "${REMOTE_USER}@${REMOTE_HOST}" "if [ ! -f ${REMOTE_DIR}/.env ]; then echo '⚠️ No .env found on remote. Copying local .env...'; fi"
scp ${SSH_OPTS} "${LOCAL_DIR}/.env" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/.env"

# 4. Deploy with Docker Compose
echo "🐳 Building and starting containers on remote..."
ssh ${SSH_OPTS} "${REMOTE_USER}@${REMOTE_HOST}" "export PATH=\$PATH:/usr/local/bin && cd ${REMOTE_DIR} && docker compose -f docker-compose.prod.yml up -d --build --remove-orphans"

# 5. Health Check / Status
echo "🏥 Checking service status..."
ssh ${SSH_OPTS} "${REMOTE_USER}@${REMOTE_HOST}" "export PATH=\$PATH:/usr/local/bin && cd ${REMOTE_DIR} && docker compose -f docker-compose.prod.yml ps"

echo "✅ Deployment completed successfully!"
echo "🌍 Frontend: http://${REMOTE_HOST}:3000"
echo "🔧 Backend Docs: http://${REMOTE_HOST}:8000/docs"
