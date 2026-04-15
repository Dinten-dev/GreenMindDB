# Dev Tools & Workspace Management

This directory contains scripts to manage the local development environment, ensuring clean permissions and easy resets.

## 🛠 Prerequisites

- Docker & Docker Compose
- `sudo` access (for permission fixes)

## 🚀 Usage

### 1. Start Development (Prevention)
Always perform these steps to start Docker. This ensures containers run as your user, not root.

```bash
# Export UID/GID environment variables
source dev-tools/docker-env.sh

# Start containers
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

### 2. Fix Permissions (Correction)
If you encounter `EACCES` or `Permission denied` errors in `node_modules`, `dist`, or other generated folders:

```bash
./dev-tools/fix-permissions.sh
```
*Note: This script targets `~/gm_dev` by default (per project standards) but will fallback to the current directory if `~/gm_dev` is missing.*

### 3. Reset Workspace (Nuclear Option)
If the environment is completely broken (e.g., corrupted `node_modules`, persistent state issues):

```bash
./dev-tools/reset-dev.sh
```
**WARNING**: This will DELETE `~/gm_dev` and recreate clean folders. Back up uncommitted work if you are working directly in that directory.

## 📄 Scripts Overview

| Script | Purpose |
|--------|---------|
| `docker-env.sh` | Exports `UID` and `GID`. Must be sourced (`source script.sh`) or run via `. script.sh`. |
| `fix-permissions.sh` | Chowns the workspace to the current user. |
| `reset-dev.sh` | Deletes and wipes the `~/gm_dev` workspace directory. |

## ⚠️ Important Notes

- **Docker Compose**: The `docker-compose.dev.yml` is configured to read `user: "${UID}:${GID}"`. If these variables are not set, Docker might fail or default to root. **Always source `docker-env.sh` first.**
- **Path**: These scripts default to managing `~/gm_dev`. If you are working in a different path, edit the `DEV_DIR` / `TARGET_DIR` variables in the scripts.
