# Architecture Decisions

> GreenMind architecture decisions and rationale – maintained as a living document.

---

## ADR-001: FastAPI + SQLAlchemy for Backend

**Context:** Need a performant Python backend that handles IoT sensor ingestion, REST APIs, and integrates with TimescaleDB for time-series data.

**Decision:** Use FastAPI with SQLAlchemy ORM and Alembic for migrations.

**Rationale:**

- FastAPI provides automatic OpenAPI docs, async support, and Pydantic validation
- SQLAlchemy handles traditional relational queries, while raw SQL is used for TimescaleDB-specific functions (`time_bucket`)
- Alembic provides reliable, version-controlled migrations

**Status:** Accepted

---

## ADR-002: httpOnly Cookie JWT Authentication

**Context:** Need secure authentication for a web frontend that avoids XSS token theft.

**Decision:** Store JWT tokens in httpOnly cookies with SameSite=Lax. Also support the
`Authorization` header for non-browser API clients. Signup does not establish a session until the
account verifies its email.

**Rationale:**

- httpOnly cookies prevent JavaScript access to tokens (XSS protection)
- SameSite=Lax and explicit CORS origins reduce cross-site request risk; SameSite is not a
  substitute for reviewing every state-changing browser endpoint
- Dual support (cookie + header) enables both browser and machine clients
- `COOKIE_SECURE` flag should be enabled in production for HTTPS-only cookies

**Status:** Accepted

---

## ADR-003: Schema-Router-Service Architecture

**Context:** Initial implementation had Pydantic schemas and business logic co-located in router files.

**Decision:** Separate into three layers:

- `schemas/` – Pydantic request/response models
- `routers/` – HTTP handlers (thin, delegation only)
- `services/` – Business logic and external integrations

**Rationale:**

- Schemas in routers created duplication risk and made reuse harder
- Business logic in routers made testing difficult
- Service layer enables unit testing without HTTP overhead

**Status:** Implemented. Schemas are separated and maintained services own the principal auth,
ingest, gateway, firmware, plant, observation, and WAV workflows.

---

## ADR-004: TimescaleDB for Time-Series

**Context:** Need efficient storage and querying of high-frequency sensor readings.

**Decision:** Use TimescaleDB extension on PostgreSQL for the `sensor_reading` hypertable.

**Rationale:**

- Native `time_bucket` aggregation for 7d/30d chart views
- Compression and retention policies for long-term storage
- Full PostgreSQL compatibility (no separate database)
- Continuous aggregates for future real-time dashboards

**Status:** Accepted

---

## ADR-005: Docker Compose for Development and Deployment

**Context:** Need reproducible environments for development, CI, and production.

**Decision:** Use Docker Compose with explicit deployment profiles:

- `docker-compose.yml` – canonical local full stack;
- `docker-compose.staging.yml` / `docker-compose.prod.yml` – VPS frontend and backend stacks,
  reached through host Nginx;
- `compose/docker-compose.yml` – optional backend-only Caddy profile, without Next.js.

**Rationale:**

- Single command startup (`docker compose up -d --build`)
- Consistent environments across team members
- Staging and production keep database, MinIO, application, and metrics ports on localhost and
  expose only the host reverse proxy
- The optional Caddy profile keeps database, MinIO, and Prometheus on its internal network
- Each profile has a distinct fixed private bridge; Uvicorn trusts forwarding headers only from
  the exact bridge gateway or fixed application-proxy address, preserving client-IP rate limits
  without accepting spoofed headers from arbitrary peers

**Status:** Accepted

---

## ADR-006: Structured Logging

**Context:** Need consistent, parseable logs for debugging and future observability.

**Decision:** Use structured key=value format with configurable log levels.

**Rationale:**

- Machine-parseable format compatible with log aggregation tools
- Extra fields support for request context (duration, user ID, etc.)
- Configurable via `LOG_LEVEL` environment variable
- Request middleware logs method, path, status, and duration for every request

**Status:** Implemented

---

## ADR-007: Device Pairing via Short-Lived Codes

**Context:** IoT devices (Raspberry Pi gateways) need to securely register with the platform.

**Decision:** Time-limited pairing codes (6 chars, 10 min expiry) with API key exchange.

**Rationale:**

- Short codes are easy to enter on constrained devices
- Time-limited prevents code reuse attacks
- The one-time key uses `gmk_<gateway-id>_<secret>`; only a bcrypt hash is stored, while legacy
  keys remain accepted temporarily for controlled rotation
- Devices authenticate subsequent requests via `X-Api-Key` header

**Status:** Accepted

---

## ADR-008: Server-side email processing via Resend

**Context:** Initial prototypes used frontend `mailto:` links and SMTP-specific configuration,
which exposed addresses, fragmented validation, and complicated credential rotation.

**Decision:** Process verification and contact email in the backend through Resend. Configure it
with `RESEND_API_KEY`, `EMAIL_FROM`, `FRONTEND_URL`, and `CONTACT_FORM_TO`; keep honeypot and
Pydantic validation at the API boundary.

**Rationale:**

- Prevents spam via backend validation and honeypot fields
- Protects actual receiving email addresses from scraping
- Allows provider credentials to rotate through host environment configuration without touching
  source code
- Supports the mandatory verification-before-login account lifecycle

**Status:** Implemented

---

## ADR-009: Separate sensor, gateway, and server repositories

**Context:** ESP32 firmware, Raspberry Pi edge software, and the server have different hardware,
build, release, and failure constraints, but share measurement and management protocols.

**Decision:** Maintain GreenMindArdu, GreenMindRPI, and GreenMindDB as three deployable
repositories. Treat sensor payloads, gateway APIs, measurement IDs, WAV formats, OTA metadata,
and signing contracts as compatibility boundaries.

**Rationale:**

- Each target keeps a focused build and deployment toolchain
- Gateway buffering can evolve without coupling server deployment to hardware builds
- Protocol changes require explicit consumer review rather than accidental cross-target imports

**Status:** Accepted
