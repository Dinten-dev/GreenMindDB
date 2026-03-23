#!/usr/bin/env bash
set -e

# Load env variables if .env exists
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

DB_USER=${POSTGRES_USER:-postgres}
DB_NAME=${POSTGRES_DB:-greenmind}
BACKUP_DIR="./backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/backup_${TIMESTAMP}.sql"

mkdir -p "$BACKUP_DIR"

echo "Creating database backup to $BACKUP_FILE..."
docker compose exec -T postgres pg_dump -U "$DB_USER" "$DB_NAME" > "$BACKUP_FILE"
# Check if file has content
if [ -s "$BACKUP_FILE" ]; then
    echo "Backup successfully created: $BACKUP_FILE"
else
    echo "Error: Backup file is empty. Check if postgres container is running."
    rm "$BACKUP_FILE"
    exit 1
fi
