#!/bin/bash
set -euo pipefail

# The prompt specified ~/gm_dev. 
# WARNING: This script deletes the directory. 
# We must be extremely careful not to delete the wrong thing.

DEV_DIR="$HOME/gm_dev"

# Safety check: Ensure DEV_DIR is not empty or root
if [ -z "$DEV_DIR" ] || [ "$DEV_DIR" = "/" ] || [ "$DEV_DIR" = "$HOME" ]; then
  echo "Invalid DEV_DIR: $DEV_DIR"
  exit 1
fi

if [ ! -d "$DEV_DIR" ]; then
    echo "Directory $DEV_DIR does not exist. Nothing to reset."
    # Optional: Offer to create it? The prompt implies resetting an EXISTING workspace.
    # But if we want to bootstrap, we might want to create it.
    # For now, just exit or create empty.
    mk_choice="n"
    read -p "$DEV_DIR not found. Create it? (y/n) " mk_choice
    if [ "$mk_choice" = "y" ]; then
        mkdir -p "$DEV_DIR"
        echo "Created $DEV_DIR"
    fi
    exit 0
fi

echo "WARNING: This will completely DELETE and recreate: $DEV_DIR"
read -p "Are you sure? (y/N) " confirmation
if [ "$confirmation" != "y" ]; then
    echo "Aborted."
    exit 1
fi

echo "Resetting workspace: $DEV_DIR"

# Remove the directory
sudo rm -rf "$DEV_DIR"

# Recreate the directory
mkdir -p "$DEV_DIR"

# Fix permissions on the new empty directory (it should be user-owned anyway if mkdir was run by user, 
# but if sudo was involved in parent... well mkdir is run as user here).
# Just to be sure, per prompt requirements:
sudo chown -R $(id -u):$(id -g) "$DEV_DIR"

echo "Workspace reset complete at $DEV_DIR"
