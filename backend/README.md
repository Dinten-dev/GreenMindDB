# GreenMind backend

Python 3.12 APIs and workers for Gateway and Direct data, tenant administration and authenticated visualization.
The frontend is in `../frontend/`; firmware belongs to the separate sensor and Pi repositories.

## Run locally

The recommended complete stack is [the root Docker Compose setup](../README.md#local-development).
For native development, supply database, object-store and authentication settings from the reviewed
[root environment template](../.env.example). Environment files are private local configuration.

From the repository root:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Before `alembic upgrade head`, confirm `DATABASE_URL` selects the local development database.
PostgreSQL and MinIO must be available; native startup does not initialize infrastructure.
Development OpenAPI: `http://127.0.0.1:8000/docs`; health: `http://127.0.0.1:8000/health`.
Production disables interactive API documentation.

## Configuration and migrations

Minimum configuration includes `DATABASE_URL`, a random `JWT_SECRET_KEY` of at least 32 characters,
explicit frontend/CORS origins and S3 connection settings. Configure email delivery before onboarding accounts.
Signup creates an unverified tenant owner; email verification is required before login.
Platform administration is a separate role with [an explicit bootstrap procedure](../docs/development.md#one-time-platform-admin-bootstrap).

`alembic/` is the canonical schema history. Containers apply reviewed migrations before service startup.
Direct has its own settings and migration history under `app/direct/`; use its reviewed Compose/release procedure.
Do not apply migrations against a different service database or add ad-hoc schema/seed scripts.

## Service entry points

| Entry point | Responsibility |
| --- | --- |
| `app.main:app` | Gateway receiving and application API |
| `app.direct.main:app` | Separate authenticated binary Direct receiver |
| `python -m app.direct.worker` | Direct chunks to segments/WAVs and derived metadata |
| `app.visualization.api:app` | Authenticated dashboard/read service |

Consult [Direct contracts](../docs/direct-to-cloud.md) and the checked
[release procedure](../deploy/release/README.md) before operating separate services.
The default API and Direct receiver are not interchangeable processes.

## API boundaries

| Boundary | Purpose |
| --- | --- |
| `/api/v1/auth` | Signup, verification, login and user lifecycle |
| `/api/v1/organizations`, `/zones`, `/plants`, `/sensors`, `/gateways` | Tenant resources and pairing |
| `/api/v1/ingest` | Authenticated Gateway batches with transactional acknowledgement |
| `/api/v1/wav` | Gateway WAV upload and authorized reads |
| `/api/v1/direct-ingest/chunks` | Direct binary ingestion on its separate receiver |
| `/api/v1/ws` | Authorized live views |
| `/api/v1/admin`, `/gateway`, `/firmware` | Existing operator/device-management APIs |

Exact schemas live in routers and Pydantic types, with development OpenAPI as the generated reference.
Gateway and Direct credentials are distinct. Existing management APIs do not authorize an OTA rollout for current manual downloads.

## Source layout

| Directory | Responsibility |
| --- | --- |
| `app/routers`, `app/models`, `app/schemas`, `app/services` | Application/API contracts and persistence |
| `app/direct/` | Direct receiver, database and assembly |
| `app/visualization/` | Dashboard/read service |
| `app/workers/` | Existing background processing |
| `alembic/` | Application database migrations |
| `tests/` | Unit and isolated integration tests |
| `scripts/` | Reviewed manual maintenance utilities |

Historical one-off tools are in [the repository archive](../archive/README.md), outside startup and onboarding.

## Tests

From the repository root with native dependencies installed:

```bash
make test-backend
make test-cov
```

From `backend/`:

```bash
python -m ruff check app/ tests/
python -m ruff format --check app/ tests/
```

Docker-backed tests: `./scripts/run_docker_tests.sh` from the repository root.
CI additionally checks real PostgreSQL Direct concurrency and Legacy migration compatibility.
See [test configuration](pyproject.toml), [testing details](../docs/testing.md)
and [ingestion/completeness acceptance](../docs/runbooks/peter-feedback-2026-10-10.md).

## Release status

Use [current source downloads](../docs/downloads.md) and exact revision manifests.
The published source includes the merged ingestion/completeness work; archive offloading/deletion changes
remain in a separate validation branch and are not part of this snapshot.
A repository merge does not establish public dashboard activation or field hardware acceptance.
