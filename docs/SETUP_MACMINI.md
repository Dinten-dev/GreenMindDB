# GreenMindDB Mac mini Setup

## Ziel
Lokaler production-naher Stack auf dem Mac mini mit:
- Reverse Proxy (Caddy + TLS)
- FastAPI API
- PostgreSQL 16 + TimescaleDB
- MinIO (S3-kompatibel)
- Optional Prometheus + Grafana

Die Migration auf externen Server erfolgt später 1:1 über ENV/Domain-Änderungen.

## 1) Voraussetzungen
- Docker Desktop oder Docker Engine + Compose Plugin
- Freie Ports (Standard): `80`, `443`

## 2) Konfiguration
```bash
cd /Users/traver/Library/Mobile Documents/com~apple~CloudDocs/FHNW/GreenMindDB
cp compose/.env.example compose/.env
```

Danach `compose/.env` anpassen:
- `POSTGRES_PASSWORD`
- `INGEST_TOKEN`
- `MINIO_ROOT_PASSWORD`
- `S3_SECRET_ACCESS_KEY`
- `GREENMIND_DOMAIN` (z. B. `greenmind.example.com`)

## 3) Stack starten
```bash
cd /Users/traver/Library/Mobile Documents/com~apple~CloudDocs/FHNW/GreenMindDB/compose
docker compose --env-file .env up -d --build
```

## 4) Monitoring optional starten
```bash
docker compose --env-file .env --profile monitoring up -d
```

Grafana läuft hinter dem Proxy unter:
- `https://<GREENMIND_DOMAIN>/grafana/`

## 5) Verifikation
```bash
# API Health
curl -k https://<GREENMIND_DOMAIN>/health

# OpenAPI
curl -k https://<GREENMIND_DOMAIN>/openapi.json
```

## 6) DB/Migrations
Die API führt beim Start automatisch aus:
```bash
alembic upgrade head
```

Dabei werden erstellt:
- Stammdaten-Tabellen (`greenhouse`, `zone`, `plant`, `device`, `sensor`)
- Hypertables (`plant_signal_1hz`, `env_measurement`)
- Event/Object/Export/Ingress-Logs
- Indizes und Continuous Aggregates (`1m`, `15m`)

## 7) Backups
### PostgreSQL Dump
```bash
cd /Users/traver/Library/Mobile Documents/com~apple~CloudDocs/FHNW/GreenMindDB/compose
docker compose --env-file .env exec -T db \
  pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backup-greenmind.sql
```

### MinIO Objekte
Empfohlen via `mc mirror` oder S3-kompatiblem Backup-Tool gegen Bucket `S3_BUCKET`.

## 8) Updates
```bash
cd /Users/traver/Library/Mobile Documents/com~apple~CloudDocs/FHNW/GreenMindDB/compose
docker compose --env-file .env pull
docker compose --env-file .env up -d --build
```

## 9) Test-Kommandos
```bash
# Integrationstests gegen Docker-Stack (nutzt compose/.env.test)
cd /Users/traver/Library/Mobile Documents/com~apple~CloudDocs/FHNW/GreenMindDB
pytest -m integration backend/tests/test_macmini_stack.py
```

## 10) Migration auf externen Server
Nur ENV/DNS anpassen:
- `GREENMIND_DOMAIN`
- `S3_PROVIDER`, `S3_ENDPOINT`, `S3_REGION`, Bucket/Credentials
- DB/Secrets

Compose-Struktur bleibt gleich.
