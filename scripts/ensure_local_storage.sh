#!/usr/bin/env bash
set -Eeuo pipefail

# Define forbidden paths (iCloud)
FORBIDDEN_PATHS=("Mobile Documents" "com~apple~CloudDocs")
CURRENT_DIR="$(pwd -P)"

# Default Local Data Root
if [[ -z "${LOCAL_DATA_ROOT:-}" ]]; then
    export LOCAL_DATA_ROOT="${HOME}/LocalData/greenmind"
    echo "LOCAL_DATA_ROOT not set. Defaulting to: $LOCAL_DATA_ROOT"
fi

# Function to check if a path contains forbidden strings
check_path() {
    local path="$1"
    for forbidden in "${FORBIDDEN_PATHS[@]}"; do
        if [[ "$path" == *"$forbidden"* ]]; then
            echo "ERROR: Data path '$path' is inside iCloud ($forbidden). This will cause corruption and performance issues."
            return 1
        fi
    done
    return 0
}

echo "=== ensuring local storage ==="
echo "Project Path: $CURRENT_DIR"

# Check if we are physically in iCloud (just a warning, as code can be in iCloud but data MUST NOT)
if ! check_path "$CURRENT_DIR"; then
    echo "WARNING: Your project code is in iCloud. This is okay for code, but strictly forbidden for database/volume data."
    echo "Proceeding because we will use LOCAL_DATA_ROOT for volumes."
fi

# Check LOCAL_DATA_ROOT
if [[ "$LOCAL_DATA_ROOT" != /* || "$LOCAL_DATA_ROOT" == "/" ]]; then
    echo "CRITICAL ERROR: LOCAL_DATA_ROOT must be a non-root absolute path."
    exit 1
fi
if ! check_path "$LOCAL_DATA_ROOT"; then
    echo "CRITICAL ERROR: LOCAL_DATA_ROOT '$LOCAL_DATA_ROOT' is inside iCloud."
    echo "Please set LOCAL_DATA_ROOT to a local path (e.g., /Users/username/LocalData/greenmind) in your .env file."
    exit 1
fi

# Create private data directories. Database and object-store contents may hold
# credentials, account data, and raw measurements.
echo "Creating local data directories at $LOCAL_DATA_ROOT..."
mkdir -p -m 700 "$LOCAL_DATA_ROOT/postgres" "$LOCAL_DATA_ROOT/minio"
chmod 700 "$LOCAL_DATA_ROOT" "$LOCAL_DATA_ROOT/postgres" "$LOCAL_DATA_ROOT/minio"

# Export for next steps
export PGDATA_DIR="$LOCAL_DATA_ROOT/postgres"
export MINIO_DATA_DIR="$LOCAL_DATA_ROOT/minio"

echo "✅ Local storage prepared:"
echo "   Postgres: $PGDATA_DIR"
echo "   MinIO:    $MINIO_DATA_DIR"
