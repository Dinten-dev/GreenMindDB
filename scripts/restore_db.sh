#!/usr/bin/env bash
set -e

if [ -z "$1" ]; then
  echo "Usage: ./scripts/restore_db.sh <path_to_backup.sql>"
  exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "Error: Backup file $BACKUP_FILE does not exist."
  exit 1
fi

if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

DB_USER=${POSTGRES_USER:-postgres}
DB_NAME=${POSTGRES_DB:-greenmind}

echo "Restoring database from $BACKUP_FILE..."
cat "$BACKUP_FILE" | docker compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME"
echo "Restore successfully completed."
