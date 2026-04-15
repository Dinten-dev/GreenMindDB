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

**Decision:** Store JWT tokens in httpOnly cookies with SameSite=Lax. Also support Authorization header for API clients.

**Rationale:**
- httpOnly cookies prevent JavaScript access to tokens (XSS protection)
- SameSite=Lax prevents CSRF for state-changing requests
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

**Status:** Implemented (schemas fully extracted; services layer started with `email_service`)

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

**Decision:** Use Docker Compose with environment-specific files:
- `docker-compose.yml` – local development
- `compose/docker-compose.yml` – production with Caddy reverse proxy

**Rationale:**
- Single command startup (`docker compose up -d --build`)
- Consistent environments across team members
- Production compose adds TLS (Caddy), monitoring (Prometheus), and hardened settings
- Path to Kubernetes/K3s migration via manifest generation

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
- API key is bcrypt-hashed in the database (only returned once during pairing)
- Devices authenticate subsequent requests via `X-Api-Key` header

**Status:** Accepted

---

## ADR-008: Server-Side Email Processing via SMTP

**Context:** Initial prototypes used frontend `mailto:` links which exposed personal email addresses to scrapers, offered no input validation, and provided a poor user experience. Previously committed SMTP credentials in `.env` also posed a security risk.

**Decision:** Migrate all mail processing to the backend using generic SMTP environment variables (e.g., Gmail App Passwords) with strict Pydantic validation (including hidden honeypot fields) on dedicated API endpoints.

**Rationale:**
- Prevents spam via backend validation and honeypot fields
- Protects actual receiving email addresses from scraping
- Allows easy, secure rotation of SMTP credentials via environment variables without touching code
- Provides a premium, seamless frontend User Experience native to the web app

**Status:** Implemented
