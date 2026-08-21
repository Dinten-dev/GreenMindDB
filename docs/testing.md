# Testing GreenMindDB

This document is the maintained command reference for the backend, frontend, and Docker-backed
tests. Run commands from the repository root unless a section says otherwise.

## Supported toolchain

| Component | CI runtime | Test and quality tools |
|---|---|---|
| Backend | Python 3.12 | pytest, pytest-cov, Ruff |
| Frontend | Node.js 24 | Jest 30, React Testing Library, ESLint 9, Prettier 3, TypeScript |
| Integration | Docker 24+ with Compose v2 | `docker-compose.test.yml` |

Install local dependencies once:

```bash
make setup
```

`make setup` copies `.env.example` only when `.env` does not already exist. Replace every
`CHANGE_ME` value before starting services. Test-only secrets in the commands below are fixed,
non-production values and must never be reused for a deployment.

## Fast local verification

```bash
make test
make lint
make format-check
```

`make test` runs the non-Docker backend suite and the frontend Jest suite. It does not start
PostgreSQL, TimescaleDB, MinIO, or the full application stack.

## Backend tests

The backend suite is under `backend/tests/`. Its default fixtures use a temporary SQLite
database and FastAPI dependency overrides. This keeps most tests fast, but it does not reproduce
TimescaleDB extensions, PostgreSQL locking, MinIO behavior, or network boundaries.

Run the same non-integration selection used by CI:

```bash
make test-backend
```

Run it with the CI coverage threshold:

```bash
make test-cov
```

The coverage command enforces 60% and writes the local HTML report to
`backend/htmlcov/index.html`.

Run a focused test while developing:

```bash
cd backend
SKIP_DOCKER_TESTS=1 \
JWT_SECRET_KEY=ci-test-secret-key-that-is-at-least-32-chars \
python -m pytest tests/test_auth_router.py -v
```

Useful test areas include:

- account signup, verification, login, cookies, and authorization;
- request validation and boundary analysis;
- gateway authentication, desired state, remote commands, and release handling;
- sensor ingestion, WAV metadata, and plant-evaluation behavior;
- public observation tokens and tenant isolation;
- configuration and production-safety validation.

When adding a test, prefer behavior visible at an API, service, parser, storage, or trust
boundary. Keep each test independent and include rejection cases for malformed or unauthorized
input.

### Docker-backed backend tests

The integration marker is reserved for tests that need the Docker test stack. Run it explicitly:

```bash
make test-docker
```

This invokes `scripts/run_docker_tests.sh`, which creates the isolated
`docker-compose.test.yml` stack and removes its volumes afterward. Do not point the test stack at
a development or production database. The test runner builds the `test` target in
`backend/Dockerfile`, installs `requirements-dev.txt` during the image build, and runs pytest as
the unprivileged application user; it does not install packages when the container starts.

To select integration tests manually:

```bash
cd backend
python -m pytest tests/ -v -m integration
```

## Frontend tests

Install exactly the locked dependency graph and run Jest:

```bash
cd frontend
npm ci
npm test -- --ci --passWithNoTests
```

Other maintained commands are:

```bash
npm run test:watch
npm run test:coverage
npm run lint
npm run format:check
npm run type-check
npm run build
```

Jest uses jsdom, React Testing Library, and the browser API mocks in `jest.setup.ts`. Tests cover
shared components and user-facing flows including account verification, contact, and early
access. Prefer queries and assertions that reflect what a user can observe; avoid testing React
implementation details.

## CI quality gates

`.github/workflows/ci.yml` runs for pushes and pull requests to `main` and `develop`.

The backend job uses Python 3.12 and performs:

1. hash-locked application dependencies from `backend/requirements.lock`, then pinned test and
   quality tools from `backend/requirements-dev.txt`;
2. `python -m ruff check app/ tests/`;
3. `python -m ruff format --check app/ tests/`;
4. non-integration pytest with a 60% coverage gate;
5. upload of `coverage.xml` as an artifact.

The frontend job uses Node.js 24 and performs:

1. `npm ci`;
2. ESLint and Prettier checks;
3. TypeScript type-checking;
4. a high-severity production dependency audit;
5. the Next.js production build;
6. Jest with coverage and artifact upload.

CI deliberately skips Docker-marked backend tests. Run them locally or in an appropriately
isolated integration environment before merging changes that depend on PostgreSQL, TimescaleDB,
MinIO, migrations, or container networking.

## What requires explicit integration coverage

SQLite unit fixtures cannot establish all production properties. Changes in these areas require
targeted integration verification:

- Alembic migrations and TimescaleDB hypertables;
- transaction isolation, row locking, and concurrent idempotency;
- MinIO/S3 upload, download, limits, and cleanup;
- WebSocket connection limits and multi-client behavior;
- gateway-to-server retries and release downloads;
- reverse-proxy, cookie, CORS, and TLS behavior.

Never report a Docker, firmware, or hardware path as passing when only the SQLite/Jest suites were
run.
