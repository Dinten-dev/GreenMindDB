# GreenMind — Universal Agriculture Platform

> Plant bioelectrical sensing platform for universal agriculture — greenhouses, open fields, vertical farms, and orchards. Real-time monitoring and predictive analytics powered by ESP32 sensors, Raspberry Pi gateways, and a modern web stack.

[![CI](https://github.com/<owner>/GreenMindDB/actions/workflows/ci.yml/badge.svg)](https://github.com/<owner>/GreenMindDB/actions/workflows/ci.yml)

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Architecture](#architecture)
3. [Tech Stack](#tech-stack)
4. [Project Structure](#project-structure)
5. [Prerequisites](#prerequisites)
6. [Local Setup](#local-setup)
7. [Development](#development)
8. [Testing](#testing)
9. [Linting & Formatting](#linting--formatting)
10. [Build & Deployment](#build--deployment)
11. [Environment Variables](#environment-variables)
12. [Branching Strategy](#branching-strategy)
13. [Git Workflow](#git-workflow)
14. [Pull Request Rules](#pull-request-rules)
15. [CI/CD](#cicd)
16. [Troubleshooting](#troubleshooting)
17. [Security](#security)

---

## Project Overview

GreenMind is a full-stack platform for processing, storing, and analyzing **bioelectrical plant signals** across diverse agricultural environments. The system ingests data from ESP32 sensors via Raspberry Pi gateways into a TimescaleDB-backed FastAPI service, visualized on a modern Next.js frontend.

**Goals:**
- Real-time sensor data ingestion and visualization
- Predictive analytics for agricultural yield optimization
- Multi-zone management (Greenhouse, Open Field, Vertical Farm, Orchard) with geodata
- Secure JWT-based authentication with role-based access

---

## Architecture

```mermaid
graph TD;
    Sensors[ESP32 Sensors] -->|USB / Serial| Pi[Raspberry Pi Gateway]
    Pi -->|POST /api/v1/ingest| API[FastAPI Backend :8000]
    API -->|SQLAlchemy| DB[(TimescaleDB :5432)]
    UI[Next.js Frontend :3000] -->|REST API| API
    API -->|Resend API| Email[Email Notifications]
```

### Data Flow

1. **ESP32 sensors** capture bioelectrical signals from plants
2. **Raspberry Pi gateway** aggregates and forwards readings via REST
3. **FastAPI backend** validates, stores to TimescaleDB, and serves data
4. **Next.js frontend** renders real-time dashboards and management UIs

---

## Tech Stack

| Layer          | Technology                                       |
|----------------|--------------------------------------------------|
| **Frontend**   | Next.js 14, TypeScript, TailwindCSS, Recharts    |
| **Backend**    | FastAPI, SQLAlchemy, Alembic, Pydantic            |
| **Database**   | PostgreSQL 15 + TimescaleDB                       |
| **Auth**       | JWT (httpOnly cookies), bcrypt                    |
| **Email**      | Resend (transactional email API)                  |
| **Deployment** | Docker Compose                                    |
| **CI/CD**      | GitHub Actions                                    |
| **Linting**    | ruff, black (Python) · ESLint, Prettier (TS)      |

---

## Project Structure

```
GreenMindDB/
├── backend/                  # FastAPI Python backend
│   ├── app/
│   │   ├── main.py           # Application entrypoint
│   │   ├── config.py         # Settings (pydantic-settings)
│   │   ├── database.py       # SQLAlchemy engine & session
│   │   ├── auth.py           # JWT, password hashing, auth deps
│   │   ├── logging_config.py # Structured logging setup
│   │   ├── models/           # SQLAlchemy ORM models (Zone, Gateway, Sensor)
│   │   ├── routers/          # API route handlers (zones, gateways, sensors)
│   │   └── services/         # Business logic (zone, gateway, email)
│   ├── alembic/              # Database migrations
│   ├── scripts/              # Utility scripts (seeding, import)
│   ├── tests/                # pytest test suite
│   ├── Dockerfile
│   ├── requirements.txt
│   └── pyproject.toml        # ruff, black, pytest config
├── frontend/                 # Next.js TypeScript frontend
│   ├── src/
│   │   ├── app/              # Next.js App Router pages
│   │   ├── components/       # Reusable UI components
│   │   ├── contexts/         # React context providers
│   │   ├── hooks/            # Custom React hooks
│   │   ├── lib/              # API client, utilities
│   │   └── types/            # Shared TypeScript types
│   ├── public/               # Static assets
│   ├── Dockerfile
│   └── package.json
├── compose/                  # Production Docker Compose config
├── db/                       # Database init scripts
├── docs/                     # Project documentation
├── scripts/                  # Dev/deploy helper scripts
├── .github/                  # CI/CD, PR & issue templates
├── docker-compose.yml        # Local development compose
├── Makefile                  # Developer convenience commands
├── .env.example              # Environment template
└── README.md
```

---

## Prerequisites

- **Docker** ≥ 24.0 and **Docker Compose** ≥ 2.20
- **Python** ≥ 3.11 (for local backend development)
- **Node.js** ≥ 20 and **npm** ≥ 10 (for local frontend development)
- **Git** ≥ 2.40

---

## Local Setup

### 1. Clone the Repository

```bash
git clone <repo-url>
cd GreenMindDB
```

### 2. Configure the Environment

```bash
cp .env.example .env
```

Edit `.env` and set your values. At minimum, configure:
- `POSTGRES_PASSWORD` — a strong database password
- `JWT_SECRET_KEY` — at least 32 random characters

> **⚠️ macOS iCloud Users:** If this project is inside an iCloud-synced folder, you **must** set `PGDATA_DIR` to a path **outside** iCloud to prevent database corruption:
> ```env
> LOCAL_DATA_ROOT=/Users/yourname/LocalData/greenmind
> PGDATA_DIR=/Users/yourname/LocalData/greenmind/postgres_data
> ```

### 3. Start the Application Stack

```bash
make dev
# or: docker compose up -d --build
```

This will:
1. Pull and start the TimescaleDB database
2. Build and start the FastAPI backend (runs Alembic migrations automatically)
3. Build and start the Next.js frontend

### 4. Seed Demo Data (Optional)

```bash
docker compose exec backend python -m scripts.seed_data
```

### 5. Access the Platform

| Service               | URL                                  |
|-----------------------|--------------------------------------|
| Frontend Dashboard    | http://localhost:3000                 |
| Backend API Docs      | http://localhost:8000/docs            |
| Health Check          | http://localhost:8000/health          |

**Demo credentials:** `demo@greenmind.io` / `Demo1234`

---

## Development

### Running Individual Services

```bash
# Backend only (local Python)
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Frontend only (local Node)
cd frontend
npm install
npm run dev
```

### Useful Commands

```bash
make dev       # Start full Docker stack
make stop      # Stop all containers
make logs      # Tail container logs
make clean     # Stop + remove volumes (full reset)
make health    # Check service health
make seed      # Seed demo data
```

---

## Testing

### Backend Tests

The most robust way to run the entire backend test suite (unit + integration) is inside a dedicated Docker container. This ensures testing matches production dependencies without relying on local Python setups:

```bash
./scripts/run_docker_tests.sh
```

Alternatively, to run tests locally (requires Python 3.11+ and PostgreSQL libraries):
```bash
make test
# or: cd backend && python -m pytest tests/ -v
```

Integration tests that require Docker can be bypassed locally with:
```bash
SKIP_DOCKER_TESTS=1 pytest tests/ -v
```

### Frontend Tests

```bash
cd frontend && npm test
```

---

## Linting & Formatting

### Backend

```bash
make lint      # Run ruff linter
make format    # Format with black

# Or manually:
cd backend
python -m ruff check app/ tests/
python -m black app/ tests/
```

### Frontend

```bash
cd frontend
npm run lint      # ESLint via Next.js
npm run format    # Prettier
```

### Pre-commit Hooks (Optional)

```bash
pip install pre-commit
pre-commit install
```

This will automatically run linting and formatting checks before each commit.

---

## Build & Deployment

### Docker Build

```bash
make build     # Build all Docker images
# or: docker compose build
```

### Production Deployment (Hetzner)

Deploy to the Hetzner production server with the automated script:
```bash
./scripts/deploy_production.sh
```

This will:
1. Sync code via `rsync` (excludes `node_modules`, `.env`, etc.)
2. **Skip** overwriting the production `.env` (protects secrets)
3. Build Docker images on the server
4. Run Alembic migrations automatically
5. Restart all containers

> **⚠️ Important:** The `.env` on the server is never overwritten by deploy.
> To change production environment variables, SSH into the server and edit `~/GreenMindDB/.env` directly,
> then restart with `docker compose -f docker-compose.prod.yml restart backend`.

---

## Environment Variables

All configuration is via environment variables. Copy `.env.example` to `.env` and customize:

| Variable                          | Description                                 | Default                       |
|-----------------------------------|---------------------------------------------|-------------------------------|
| `POSTGRES_USER`                   | Database username                           | `greenmind`                   |
| `POSTGRES_PASSWORD`               | Database password                           | *(required)*                  |
| `POSTGRES_DB`                     | Database name                               | `greenminddb`                 |
| `POSTGRES_PORT`                   | Exposed database port                       | `5432`                        |
| `BACKEND_PORT`                    | Exposed backend port                        | `8000`                        |
| `FRONTEND_PORT`                   | Exposed frontend port                       | `3000`                        |
| `CORS_ORIGINS`                    | Allowed CORS origins (comma-separated)      | `http://localhost:3000`       |
| `JWT_SECRET_KEY`                  | JWT signing key (min 32 chars)              | *(required)*                  |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Token validity in minutes                   | `10080` (7 days)              |
| `COOKIE_SECURE`                   | Set `true` in production (HTTPS)            | `false`                       |
| `LOCAL_DATA_ROOT`                 | Local data storage root (macOS iCloud fix)  | `./data`                      |
| `PGDATA_DIR`                      | PostgreSQL data directory                   | `./postgres_data`             |
| `RESEND_API_KEY`                  | Resend API key for transactional email      | *(optional)*                  |
| `EMAIL_FROM`                      | Sender address for outgoing email           | `onboarding@biolingo.org`     |
| `FRONTEND_URL`                    | Frontend URL (used in email links)          | `https://biolingo.org`        |
| `CONTACT_FORM_TO`                 | Recipient for contact/early-access emails   | *(optional)*                  |

> **🔒 Never commit `.env` files with real credentials.** Use `.env.example` as a template.

---

## Branching Strategy

We use a **branch-based workflow** with two long-lived branches:

```
main ──────────────────────────────────────  (stable, production)
 └── develop ──────────────────────────────  (integration)
      ├── feature/live-sensor-stream
      ├── fix/api-validation
      ├── hotfix/login-crash
      ├── refactor/backend-services
      ├── docs/readme-update
      └── chore/update-dependencies
```

| Branch      | Purpose                                      |
|-------------|----------------------------------------------|
| `main`      | Stable, production-ready – no direct commits  |
| `develop`   | Integration branch for ongoing work           |
| `feature/*` | New functionality                             |
| `fix/*`     | Bug fixes                                     |
| `hotfix/*`  | Urgent production fixes (from `main`)         |
| `refactor/*`| Code improvements                             |
| `docs/*`    | Documentation changes                         |
| `chore/*`   | Maintenance / tooling                         |

---

## Git Workflow

### Starting a New Feature

```bash
# 1. Ensure you're up to date
git checkout develop
git pull origin develop

# 2. Create your feature branch
git checkout -b feature/my-feature

# 3. Work on your changes
# ... edit files ...

# 4. Stage and commit
git add .
git commit -m "feat: add sensor streaming endpoint"

# 5. Push to remote
git push -u origin feature/my-feature

# 6. Create a Pull Request on GitHub → target: develop
```

### Keeping Your Branch Up to Date

```bash
git fetch origin
git rebase origin/develop
```

### Creating a Release

```bash
# Merge develop into main
git checkout main
git merge develop
git tag -a v1.0.0 -m "Release 1.0.0"
git push origin main --tags
```

---

## Pull Request Rules

1. **All changes** must go through Pull Requests
2. PRs must target `develop` (never `main` directly, except hotfixes)
3. **CI must be green** before merging
4. At least **one code review approval** required
5. Use the [PR template](.github/pull_request_template.md) and fill it out completely
6. Squash-merge to keep a clean history

---

## CI/CD

GitHub Actions automatically runs on every push and PR to `main` or `develop`:

| Job          | Steps                              |
|--------------|-------------------------------------|
| **Backend**  | Install → Lint (ruff) → Format check (black) → Test (pytest) |
| **Frontend** | Install → Lint (ESLint) → Build (Next.js)                    |
| **Docker**   | Build all containers (on `main`/`develop` only)              |

Configuration: [`.github/workflows/ci.yml`](.github/workflows/ci.yml)

---

## Troubleshooting

### Containers won't start

```bash
# Check logs
docker compose logs -f

# Check specific service
docker logs -f greenminddb-backend-1
```

### Database migration errors

```bash
# Full reset: remove volumes + data directory
docker compose down -v
rm -rf /path/to/your/PGDATA_DIR
docker compose up -d --build
```

### Port conflicts

If ports 3000, 8000, or 5432 are already in use, change them in `.env`:
```env
BACKEND_PORT=8001
FRONTEND_PORT=3001
POSTGRES_PORT=5433
```

### iCloud sync issues (macOS)

PostgreSQL data **must not** be stored in an iCloud-synced folder. Set `PGDATA_DIR` to a local path outside iCloud in your `.env`.

### Backend rebuild after code changes

```bash
docker compose build backend
docker compose up -d
```

---

## Security

### Authentication & Access Control

- **Password hashing**: bcrypt via `passlib` with auto-configured work factor
- **Password complexity**: Backend enforces ≥8 chars, uppercase, lowercase, digit
- **JWT storage**: httpOnly + SameSite=Lax cookies only — **never** in localStorage
- **Token expiration**: Configurable via `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`
- **RBAC**: All API endpoints enforce `organization_id` ownership checks (prevents IDOR)
- **Rate limiting**: Login, signup, verify-email (5/min), pairing-code, register (5/min)

### IoT / Gateway Security

- **API keys**: Each gateway receives a unique `secrets.token_urlsafe(32)` key, stored as bcrypt hash
- **Pairing codes**: 6-char, 10 min TTL, single-use, `hardware_id` uniqueness enforced
- **Sensor-gateway affinity**: Ingest rejects readings from gateways that don't own the sensor MAC
- **Idempotency**: `measurement_id` prevents duplicate data ingestion

### HTTP Security Headers

| Header | Value |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Content-Security-Policy` | `default-src 'self'; frame-ancestors 'none'` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` |
| `Strict-Transport-Security` | `max-age=63072000` (production only) |

### Network & API

- **CORS**: Explicit origins only (no wildcards), `credentials: true`
- **Swagger/Docs**: Disabled in production (`ENVIRONMENT=production`)
- **Input validation**: Pydantic schemas on all endpoints, `EmailStr` for emails
- **Honeypot**: Contact/Early Access forms check `website` field against bots

### Container & Infrastructure

- All containers run as **non-root** users (`appuser`, `nextjs`)
- `no-new-privileges: true` and `cap_drop: ALL` on all services
- TimescaleDB bound to `127.0.0.1` only (not reachable from internet)

### Secrets Management

- **Never commit** `.env` files with real credentials
- All secrets loaded from environment variables
- JWT secret validator rejects defaults in production
- `COOKIE_SECURE` configurable via env (set `true` when HTTPS is enabled)

---

## API Endpoints

### Authentication
| Method | Endpoint                | Description                       |
|--------|-------------------------|-----------------------------------|
| POST   | `/api/v1/auth/signup`      | Create new account                |
| POST   | `/api/v1/auth/login`       | Login (sets httpOnly cookie)      |
| POST   | `/api/v1/auth/logout`      | Logout user                       |
| GET    | `/api/v1/auth/me`          | Get current user                  |

### Core Resources
| Method | Endpoint                           | Description                     |
|--------|------------------------------------|---------------------------------|
| GET    | `/api/v1/organizations`               | List organizations              |
| POST   | `/api/v1/organizations`               | Create organization             |
| GET    | `/api/v1/zones`                       | List zones (all types)          |
| POST   | `/api/v1/zones`                       | Create zone (type, GPS)         |
| DELETE | `/api/v1/zones/{id}`                  | Delete zone + cascade           |
| GET    | `/api/v1/zones/{id}/overview`         | Zone dashboard overview         |
| GET    | `/api/v1/gateways`                    | List gateways (`?zone_id=`)     |
| POST   | `/api/v1/gateways/pairing-code`       | Generate pairing code           |
| POST   | `/api/v1/gateways/register`           | Register a gateway              |
| POST   | `/api/v1/gateways/heartbeat`          | Gateway heartbeat (X-Api-Key)   |
| GET    | `/api/v1/sensors`                     | List sensors (`?zone_id=`)      |
| GET    | `/api/v1/sensors/{id}/data`           | Get sensor timeseries data      |
| GET    | `/api/v1/sensors/{id}/export`         | Export sensor data as ZIP       |

### Ingestion (IoT)
| Method | Endpoint          | Description                                    |
|--------|-------------------|------------------------------------------------|
| POST   | `/api/v1/ingest`     | Push sensor readings (`X-Api-Key` auth)        |

### Live Data (WebSocket)
| Method | Endpoint                    | Description                             |
|--------|-----------------------------|-----------------------------------------|
| WS     | `/api/v1/ws/zone/{id}`      | Stream live sensor readings per zone    |

### Contact & Early Access
| Method | Endpoint               | Description                                    |
|--------|------------------------|------------------------------------------------|
| POST   | `/api/v1/contact`       | Submit contact form (public, rate-limited)      |
| POST   | `/api/v1/early-access`  | Submit early-access request (public)            |
| GET    | `/api/v1/submissions`   | List all form submissions (admin only)          |

> All submissions are persisted to the database as a durable backup.
> Email notifications via Resend are sent as best-effort.
> Filter by type: `GET /api/v1/submissions?form_type=early_access`

### Gateway Pairing Flow
1. User creates a **Zone** (Greenhouse, Open Field, Vertical Farm, or Orchard)
2. User generates a 10-minute pairing code for that zone via the dashboard
3. Raspberry Pi gateway sends `POST /api/v1/gateways/register` with code + hardware serial
4. Backend validates, registers the gateway to the zone, returns an `X-Api-Key`
5. Gateway sends heartbeats via `POST /api/v1/gateways/heartbeat`
6. Gateway streams readings via `POST /api/v1/ingest` using the API key
7. Live data appears on the zone dashboard via WebSocket

## Database Backup & Restore

Automated bash scripts are provided to cleanly run `pg_dump` and `pg_restore` through the active TimescaleDB container.

### Backup Database
```bash
./scripts/backup_db.sh
```
This generates a timestamped `.sql` file in the `./backups` directory.

### Restore Database
```bash
./scripts/restore_db.sh ./backups/backup_YYYYMMDD_HHMMSS.sql
```
*Note: Restoration will apply the SQL dump to the current database.*

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
