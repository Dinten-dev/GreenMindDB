#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: ./scripts/restore_db.sh <path-to-backup.sql>" >&2
    exit 2
fi

readonly backup_file="$1"

if [[ ! -f "${backup_file}" || ! -s "${backup_file}" ]]; then
    echo "Backup is missing or empty: ${backup_file}" >&2
    exit 1
fi

echo "Restoring ${backup_file} into the database configured for this Compose stack..."
# ON_ERROR_STOP prevents a partially failed SQL stream from being reported as
# successful. Restore into a disposable database first when validating backups.
docker compose exec -T postgres sh -c \
    'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    < "${backup_file}"
echo "Restore completed successfully."
