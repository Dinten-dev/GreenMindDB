# Changelog

All notable changes to the GreenMind platform are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] — 2026-05-12

### Added
- **Plant observation system** — decoupled plant entity, short-lived QR access, and login-free mobile observation app
- **Plant evaluation system** — API endpoints, database models, and scoring logic
- **Gateway remote management (OTA)** — desired-state agent, Ed25519 signatures, staged rollouts, audit logging
- **Firmware OTA management** — dashboard, deployment tracking, rollback support
- **WAV file management** — listing, download, and configurable export date ranges
- **Apple HIG export modal** — format and range selection for sensor data export
- **Prometheus monitoring** — `/metrics` endpoint with FastAPI instrumentator
- **Pre-commit hooks** — ruff + black auto-checks before commits
- **Staging environment** — independent CI/CD pipeline at `test.green-mind.ch`

### Changed
- Replaced horizontal scroll containers with vertical flex layouts across landing and product pages
- Optimized mobile responsiveness across all pages
- Hardened `.gitignore` to untrack WAV test data and build artifacts

### Fixed
- Resolved 502 timeout for 30-day sensor data export
- Fixed TypeScript build errors and removed dead species code
- Fixed deprecated `datetime.utcnow()` calls across backend
- Resolved Alembic migration head collisions
- Fixed Nginx API proxy for cookie-based authentication

### Security
- Non-root Docker containers with `cap_drop: ALL` and `no-new-privileges`
- Rate limiting on auth, pairing, and contact endpoints
- JWT secret validator rejects defaults in production
- Gateway API key stored as bcrypt hash
- Sensor-gateway affinity prevents cross-gateway data injection

---

## [2.0.0] — 2026-04-15

### Added
- **Biosignal integration** — live sensor dashboards with online status and charts
- **Contact & early access forms** — with email notifications via Resend and database persistence
- **Secure device pairing** — captive portal provisioning with 6-char pairing codes
- **Email verification** — Resend-based transactional email for user signup
- **Comprehensive test suite** — pytest with SQLite compatibility layer

### Changed
- Migrated from SMTP to Resend for all transactional email
- Consolidated environment configuration under `pydantic-settings`

---

## [1.0.0] — 2026-03-01

### Added
- Initial full-stack platform: FastAPI backend, Next.js frontend, TimescaleDB
- Core data model: Organizations, Zones, Gateways, Sensors, SensorReading (hypertable)
- JWT authentication with httpOnly cookies
- Real-time WebSocket streaming per zone
- Docker Compose multi-environment setup (local, staging, production)
- GitHub Actions CI/CD with automated deployment to Hetzner VPS
- Alembic database migration framework
- Makefile developer convenience commands
