#!/bin/bash
set -e

# Default to current directory if not set or if ~/gm_dev doesn't exist yet
# The user prompt specifically asked for ~/gm_dev, but we should be robust.
# If ~/gm_dev exists, we use it. If not, we use the current directory (assuming we are in the project).

TARGET_DIR="$HOME/gm_dev"

if [ ! -d "$TARGET_DIR" ]; then
    echo "Warning: $TARGET_DIR does not exist. Using current directory $(pwd)."
    TARGET_DIR="$(pwd)"
fi

echo "Fixing permissions in: $TARGET_DIR"

# Using sudo to ensure we can fix root-owned files
sudo chown -R $(id -u):$(id -g) "$TARGET_DIR"

echo "Permissions fixed"
