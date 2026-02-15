# GreenMindDB – Mac Mini Setup Guide

> Local development & production setup for the GreenMindDB greenhouse monitoring stack.

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Docker + Docker Compose | ≥ 24.x | `docker compose` (V2 CLI) |
| Git | ≥ 2.x | |
| Python | ≥ 3.11 | Only for running tests locally |

## Quick Start

```bash
# 1. Clone & enter the project
cd GreenMindDB

# 2. Create .env from template
cp compose/.env.example compose/.env

# 3. Edit .env – at minimum change:
#    - POSTGRES_PASSWORD
#    - JWT_SECRET_KEY (≥32 chars)
#    - ADMIN_PASSWORD
#    - MINIO_ROOT_PASSWORD / S3_SECRET_ACCESS_KEY
nano compose/.env

# 4. Start the stack
cd compose && docker compose up -d --build

# 5. Verify
docker compose logs -f api    # watch for "Seeded admin user"
curl -k https://localhost/health
```

## Architecture

```
┌───────────────────────────── Mac Mini ──────────────────────────────┐
│                                                                     │
│  ┌─── Caddy ───┐   ┌──── FastAPI ────┐   ┌──── PostgreSQL ────┐   │
│  │ TLS (443)   │──▶│ /auth/*         │──▶│ TimescaleDB 2.17   │   │
│  │ Rate limit  │   │ /admin/*        │   │ Hypertables:       │   │
│  │             │   │ /operator/*     │   │  - plant_signal_1hz│   │
│  └─────────────┘   │ /v1/ingest/*   │   │  - env_measurement │   │
│                     │ /v1/exports/*  │   │ + continuous aggs  │   │
│                     │ /v1/media/*    │   └────────────────────┘   │
│                     │ /health        │                             │
│                     └───────┬────────┘   ┌──── MinIO ────────┐   │
│                             └───────────▶│ S3 object storage │   │
│                                          │ exports, images   │   │
│                                          └───────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Services

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `db` | `timescale/timescaledb:2.17.2-pg16` | 5432 (internal) | Primary database |
| `minio` | `minio/minio:latest` | 9000/9001 (internal) | S3 object storage |
| `api` | Custom Python 3.11 | 8000 (internal) | FastAPI backend |
| `proxy` | `caddy:2.10.2` | 80, 443 | TLS reverse proxy |
| `prometheus` | `prom/prometheus:v3.5.0` | 9090 (internal) | Metrics (optional) |
| `grafana` | `grafana/grafana:12.1.1` | 3000 (internal) | Dashboards (optional) |

## Authentication

### Login

```bash
# Get tokens
curl -k -X POST https://localhost/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@greenmind.local","password":"YOUR_ADMIN_PASSWORD"}'

# Response:
# { "access_token": "...", "refresh_token": "...", "role": "admin" }
```

### Using Tokens

```bash
# All endpoints require Authorization header:
curl -k -H "Authorization: Bearer <access_token>" https://localhost/admin/users
```

### Roles

| Role | Scope | Capabilities |
|------|-------|-------------|
| `admin` | All greenhouses | User management, master data, audit, annotation review |
| `operator` | Own greenhouse only | Dashboard, data entry, ground truth, annotations |
| `research` | Read-only | Data queries, exports |
| `viewer` | Read-only | Basic dashboard access |

## API Overview

### Auth (`/auth/*`)
- `POST /auth/login` – Email/password → JWT tokens
- `POST /auth/refresh` – Rotate refresh token
- `POST /auth/logout` – Revoke refresh token

### Admin (`/admin/*`) – requires `admin` role
- `GET/POST /admin/users` – User management
- `PATCH /admin/users/{id}` – Update role/greenhouse
- `GET/POST /admin/greenhouses` – Greenhouse CRUD
- `GET/POST /admin/zones` – Zone CRUD
- `GET/POST /admin/plants` – Plant CRUD
- `GET/POST /admin/devices` – Device CRUD
- `GET/POST /admin/sensors` – Sensor CRUD
- `GET/POST /admin/label-schemas` – Label schema management
- `GET /admin/annotations` – Review queue
- `POST /admin/annotations/{id}/review` – Approve/reject
- `GET /admin/audit` – Audit trail

### Operator (`/operator/*`) – requires `operator` or `admin`
- `GET /operator/overview` – Dashboard KPIs
- `GET /operator/plants` – Plants in operator's greenhouse
- `GET /operator/plants/{id}/signal` – Raw or aggregated signals
- `GET /operator/sensors/{id}/env` – Environment data
- `GET /operator/events` – Event listing
- `POST /operator/events` – Log new event
- `GET /operator/ground-truth` – Daily assessments
- `POST /operator/ground-truth/daily` – Create daily assessment
- `GET/POST /operator/annotations` – Annotations
- `POST /operator/annotations/{id}/submit` – Submit for review

### Ingestion (`/v1/ingest/*`) – requires `INGEST_TOKEN`
- `POST /v1/ingest/plant-signal-1hz` – Batch plant signals
- `POST /v1/ingest/env` – Batch environment data
- `POST /v1/ingest/events` – Ingest events
- All endpoints are **idempotent** via `request_id`

### Export (`/v1/exports/*`)
- `POST /v1/exports/dataset` – Create ML-ready export (Parquet ZIP)
- `GET /v1/exports/{id}/status` – Poll export status
- `GET /v1/exports/{id}/download` – Download ZIP

### Media (`/v1/media/*`)
- `POST /v1/media/presign` – Get presigned upload URL
- `POST /v1/media/commit` – Commit uploaded media metadata
- `GET /operator/media` – List media objects

## Running Tests

```bash
# Integration tests (requires Docker)
cd backend
pip install -r requirements.txt
pytest tests/ -v -m integration

# Skip Docker tests
SKIP_DOCKER_TESTS=1 pytest tests/ -v
```

## Database Migrations

```bash
# Auto-run on container start, or manually:
docker compose exec api alembic upgrade head
docker compose exec api alembic downgrade -1
```

## Monitoring (Optional)

```bash
# Start with monitoring profile
docker compose --profile monitoring up -d

# Grafana at: https://localhost/grafana/
# Prometheus at: http://localhost:9090 (internal only)
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Service unhealthy` | Check `docker compose logs <service>` |
| `Connection refused` on health | Wait 30-60s for migrations to complete |
| `401 Unauthorized` on ingest | Ensure `INGEST_TOKEN` in `.env` matches `Authorization: Bearer <token>` |
| MinIO errors | Verify `MINIO_ROOT_PASSWORD` matches `S3_SECRET_ACCESS_KEY` |
| Alembic migration error | `docker compose exec api alembic downgrade base && docker compose exec api alembic upgrade head` |
