# GreenMindDB - Plant Data Intelligence Platform

Scaleable plant telemetry, annotation, and analysis platform for greenhouse management.

## 🚀 Quick Start

```bash
# 1. Setup Environment
cp .env.example .env
# Edit .env and set secure values (POSTGRES_PASSWORD, SECRET_KEY, etc.)

# 2. Start Services (Local Storage Mode)
# CRITICAL: Do NOT use `docker compose up` directly if your project is in iCloud.
# Use the provided script to ensure database/MinIO data is stored locally.

# 1. Edit .env and set LOCAL_DATA_ROOT to a local path (e.g. ~/LocalData/greenmind)
# 2. Run the deployment script:
./scripts/deploy_local.sh

# This script will:
# - Check that your data paths are NOT in iCloud
# - Create the necessary directories
# - Start Docker with correct User/Group permissions (via dev-tools)

# 3. Access Interfaces
# - Operator Dashboard: http://localhost:3000
# - API Documentation: http://localhost:8000/docs
# - S3/MinIO Console: http://localhost:9001 (User: minioadmin, Pass: minioadmin)

```

> **Note**: This system uses **TimescaleDB** (PostgreSQL extension) for high-performance time-series data storage.

---

## 🏗️ Architecture

GreenMindDB consists of two main API layers:

1.  **MacMini / Edge API (`app.macmini`)**: Optimized for local deployment in the greenhouse. Handles high-frequency ingestion (1Hz), local dashboard (Operator), and sync/export.
2.  **Core API (`app.routers`)**: Shared logic for species management, target ranges, and legacy integrations.

---

## 📚 API Reference

### 1. Ingestion API (Edge/Device)
High-performance endpoints for IoT devices.
- `POST /v1/ingest/plant-signal-1hz`: Bulk ingest high-frequency bio-signals (1Hz).
- `POST /v1/ingest/env`: Bulk ingest environmental measurements (temp, humidity, light, soil).
- `POST /v1/ingest/events`: Log system events (pump activation, errors).

### 2. Operator Dashboard API
Endpoints powering the local management UI (`/operator`).
- **Overview**: `GET /operator/overview` (KPIs, active devices, 24h stats).
- **Visualization**: 
  - `GET /operator/plants/{id}/signal` (Raw or Aggregated 1m/15m data).
  - `GET /operator/sensors/{id}/env` (Raw or Aggregated environmental data).
  - `GET /operator/events` (Filterable event log).
- **Management**:
  - `GET /operator/greenhouses`, `/operator/zones`, `/operator/plants`, `/operator/devices`.
- **Annotations**:
  - `POST /operator/annotations`: Create draft annotations for signal segments.
  - `POST /operator/annotations/{id}/submit`: Submit annotation for review.
- **Ground Truth**:
  - `POST /operator/ground-truth/daily`: Log daily manual observations (pest, disease, growth).

### 3. Admin & Management API
Full control over the system configuration.
- `GET/POST /admin/users`: Manage users and roles (admin, operator, viewer).
- `GET/POST /admin/greenhouses`: Configure physical greenhouse locations.
- `GET/POST /admin/zones`: Manage zones within greenhouses.
- `GET/POST /admin/plants`: Register plants and assign to zones.
- `GET/POST /admin/devices`: Manage IoT gateways/devices.
- `GET/POST /admin/sensors`: Configure sensors and calibration.
- `GET/POST /admin/label-schemas`: Define annotation labels/classes.

### 4. Data Export & ML
Tools for data scientists.
- `POST /v1/exports/dataset`: Trigger async export job (Parquet format) to S3.
- `GET /v1/exports/{id}/status`: Check export job status.
- `GET /v1/exports/{id}/download`: Download completed export zip.
- `GET /species/{id}/ml/timeseries`: Direct ML-ready CSV stream (resampled).

### 5. Media API
- `POST /v1/media/presign`: Get S3 presigned URL for direct upload.
- `POST /v1/media/commit`: Register uploaded file metadata.

---

## 🧪 Development

### Directory Structure
- `backend/app/macmini`: New V2 architecture (clean separation of concerns).
- `backend/app/routers`: Legacy/Shared endpoints.
- `backend/alembic`: Database migrations.
- `frontend/`: Next.js 14 Dashboard.

### Testing
```bash
# Run backend tests
docker compose exec backend pytest

# Run type checks (mypy)
docker compose exec backend mypy app
```
