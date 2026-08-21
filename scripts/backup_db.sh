#!/usr/bin/env bash
set -Eeuo pipefail

# Backups contain credentials and other sensitive tenant data. Keep newly
# created files private even when the caller has a permissive default umask.
umask 077

readonly backup_dir="${BACKUP_DIR:-./backups}"
readonly timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
readonly backup_file="${backup_dir}/greenmind_${timestamp}.sql"
readonly temporary_file="${backup_file}.part"

if [[ -z "${backup_dir}" || "${backup_dir}" == "/" ]]; then
    echo "Refusing unsafe BACKUP_DIR: ${backup_dir:-<empty>}" >&2
    exit 2
fi

mkdir -p -- "${backup_dir}"
chmod 700 -- "${backup_dir}"

cleanup() {
    rm -f -- "${temporary_file}"
}
trap cleanup EXIT

echo "Creating private database backup at ${backup_file}..."
# POSTGRES_USER/POSTGRES_DB are expanded inside the container. Docker Compose
# loads its normal .env file without exposing or reparsing it in this shell.
docker compose exec -T postgres sh -c \
    'exec pg_dump --no-owner --no-acl -U "$POSTGRES_USER" "$POSTGRES_DB"' \
    > "${temporary_file}"

if [[ ! -s "${temporary_file}" ]]; then
    echo "Backup failed: pg_dump produced no data." >&2
    exit 1
fi

mv -- "${temporary_file}" "${backup_file}"
trap - EXIT
echo "Backup completed: ${backup_file}"
