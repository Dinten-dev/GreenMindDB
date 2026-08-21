# Optional Mac mini API deployment

This guide covers the isolated `compose/` profile: FastAPI, TimescaleDB, MinIO, Caddy, and
optional Prometheus/Grafana. It does **not** run the Next.js frontend and is not the canonical
developer stack. For ordinary local development, use the root `docker-compose.yml` and the
[README quick start](../README.md#quick-start).

## Prerequisites

- Docker 24 or newer with Docker Compose v2
- a host name that resolves to the Mac mini, or Caddy's local TLS mode for a private test host
- an Ed25519 gateway-release verification public key in PEM format
- SMTP replacement/email delivery configured through Resend before onboarding users
- a separate backup plan for PostgreSQL and MinIO data

The profile uses named Docker volumes. Treat it as a persistent backend deployment, not a
throwaway frontend preview.

## Configuration

```bash
cd GreenMindDB
cp compose/.env.example compose/.env
```

Edit `compose/.env` and replace every `change-me` value. At minimum review:

- `GREENMIND_DOMAIN`, `CADDY_TLS_MODE`, and proxy ports;
- a collision-free `GREENMIND_DOCKER_SUBNET`/`GREENMIND_DOCKER_GATEWAY`, keeping
  `CADDY_PROXY_IP` inside that subnet;
- `POSTGRES_*` and a unique `JWT_SECRET_KEY` of at least 32 characters;
- unique `MINIO_ROOT_*` administrative credentials, plus a separately provisioned
  bucket-scoped `S3_ACCESS_KEY_ID`/`S3_SECRET_ACCESS_KEY` for the backend;
- `CORS_ORIGINS` for the separately deployed frontend;
- `GATEWAY_RELEASE_SIGNING_PUBLIC_KEY_FILE`, set to an absolute host path containing the public
  verification key;
- `GRAFANA_ADMIN_*` before enabling the monitoring profile.

Keep the signing private key outside this repository and outside the application host where
practical. GreenMind verifies gateway-release signatures over the ASCII SHA-256 hexadecimal
digest; it does not sign releases with the public key.

The Compose profile intentionally disables the experimental provisioning and biosignal routers.
The application owns the fixed `greenmind-raw` and `greenmind-photos` buckets; there is no
runtime-selectable application bucket. Do not use example secrets or local Caddy certificates on
an internet-facing host.

## Validate and start

```bash
docker compose \
  --env-file compose/.env \
  -f compose/docker-compose.yml \
  config --quiet

docker compose \
  --env-file compose/.env \
  -f compose/docker-compose.yml \
  up -d --build
```

The API container applies `alembic upgrade head` before the server starts. Watch readiness with:

```bash
docker compose --env-file compose/.env -f compose/docker-compose.yml ps
docker compose --env-file compose/.env -f compose/docker-compose.yml logs -f api proxy
curl --fail --cacert /path/to/local-ca.pem https://api.example.test/health
```

For a public certificate, use the normal system trust store and omit `--cacert`. Avoid `curl -k`
outside an isolated local diagnosis because it disables certificate verification.

## Services and exposure

| Service | Image/runtime | Exposure | Purpose |
|---|---|---|---|
| `db` | TimescaleDB 2.17.2 on PostgreSQL 16 | internal network only | relational and time-series data |
| `minio` | pinned MinIO release | internal network only | S3-compatible object storage |
| `api` | Python 3.12/FastAPI | via Caddy only | `/api/v1`, health, and application metrics |
| `proxy` | Caddy 2.10.2 | configured HTTP/HTTPS ports | TLS termination and security headers |
| `prometheus` | Prometheus 3.5.0 | optional profile, internal only | metric collection |
| `grafana` | Grafana 12.1.1 | optional profile at `/grafana/` | operator dashboards |

Caddy denies public `/metrics` access. The Next.js frontend must be deployed separately and its
origin must be present in `CORS_ORIGINS`.

The API trusts forwarding headers only from the fixed `CADDY_PROXY_IP`. If the private Docker
subnet must change, update all three network values together and recreate the Compose network in
a planned maintenance window; never replace the trusted proxy list with `*`.

## Accounts and roles

There are no demo, default owner, or fixed administrator credentials. Signup creates an
unverified tenant `OWNER` and returns a detail response without establishing a session. The user
must follow the verification link and the frontend must submit the token to
`POST /api/v1/auth/verify-email` before login succeeds.

`OWNER` is organization-scoped. Fleet-wide gateway and firmware administration requires the
platform `ADMIN` role; signup cannot request that role. Follow the reviewed interactive bootstrap
procedure in the [root README](../README.md#one-time-platform-admin-bootstrap) to promote one
already verified account. Never add a fixed bootstrap password or seed a verified owner.

All maintained application routes are under `/api/v1` except `/`, `/health`, `/metrics`, and
development OpenAPI endpoints. Use the [API boundary table](../README.md#api-boundaries) or
OpenAPI in a development environment rather than relying on copied endpoint lists.

## Migrations

Migrations run automatically at container startup. To inspect or reapply the current head:

```bash
docker compose --env-file compose/.env -f compose/docker-compose.yml exec api \
  alembic current
docker compose --env-file compose/.env -f compose/docker-compose.yml exec api \
  alembic upgrade head
```

Do not use `alembic downgrade base` as a troubleshooting shortcut: it is destructive and may not
preserve production data. Back up first, inspect the failing revision, and test recovery against a
disposable database.

## Monitoring profile

```bash
docker compose \
  --env-file compose/.env \
  -f compose/docker-compose.yml \
  --profile monitoring \
  up -d
```

Grafana is routed at `https://api.example.test/grafana/` (replace the example host with
`GREENMIND_DOMAIN`). Prometheus remains on the internal
Compose network.

## Testing and operations

Unit and Docker-backed test commands are maintained in [testing.md](testing.md). The optional
deployment profile itself does not include the Next.js tests or frontend service.

Before an upgrade:

1. back up PostgreSQL and MinIO independently;
2. validate the Compose rendering with the host's real environment;
3. review Alembic revisions and gateway/server API compatibility;
4. deploy, then check `ps`, API health, Caddy logs, MinIO health, and one authenticated request;
5. keep the prior image and configuration available for a controlled rollback.

Stop services without deleting volumes:

```bash
docker compose --env-file compose/.env -f compose/docker-compose.yml stop
```

Do not add `--volumes` unless destruction of the named database, MinIO, and monitoring volumes is
explicitly intended and independently recoverable.
