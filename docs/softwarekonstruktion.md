# Softwarekonstruktion — GreenMind Projekt-Mapping

Dieses Dokument beschreibt, wie die Softwarekonstruktionsprinzipien im GreenMind-Projekt umgesetzt werden. Es dient als Referenz für das Portfoliogespräch.

---

## LE1 — Versionskontrolle mit Git

### Branching-Strategie

GreenMind verwendet einen **branch-basierten Workflow** mit zwei langlebigen Branches:

| Branch    | Zweck                                          |
|-----------|-------------------------------------------------|
| `main`    | Stabil, produktionsbereit. Keine direkten Commits. |
| `develop` | Integrationsbranch für laufende Arbeit.          |

Feature-Branches werden nach Konvention benannt:

| Prefix      | Beispiel                      |
|-------------|-------------------------------|
| `feature/`  | `feature/live-sensor-stream`  |
| `fix/`      | `fix/api-validation`          |
| `hotfix/`   | `hotfix/login-crash`          |
| `refactor/` | `refactor/backend-services`   |
| `docs/`     | `docs/readme-update`          |
| `chore/`    | `chore/update-dependencies`   |

### Pull Requests & Code Review

- Alle Änderungen gehen durch **Pull Requests** → `develop`
- **Mindestens ein Review** vor dem Merge erforderlich
- CI muss grün sein vor dem Merge
- PR-Template vorhanden unter `.github/pull_request_template.md`
- CODEOWNERS-Datei definiert Review-Zuständigkeiten

### Commit-Konventionen

Wir verwenden [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>
```

Typen: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

**Begründung**: Conventional Commits ermöglichen automatisierte Changelogs und machen die Git-History navigierbar. Im Vergleich zu freien Commit Messages ist der Mehraufwand minimal, der Nutzen für das Team aber erheblich.

→ Dateien: `CONTRIBUTING.md`, `CODEOWNERS`, `.github/pull_request_template.md`

---

## LE2 — Automation

### Build-Tool: Makefile

Alle häufigen Entwicklerkommandos sind im **Makefile** zentralisiert:

```bash
make dev          # Docker Stack starten
make test         # Backend-Tests ausführen
make test-cov     # Tests mit Coverage-Report
make lint         # Backend (ruff) + Frontend (eslint) linten
make format       # Code formatieren (black)
make health       # Service-Health prüfen
make setup        # Projekt initial einrichten
make install-hooks # Pre-commit Hooks installieren
```

**Begründung**: Ein zentrales Build-Tool reduziert die Einstiegshürde für neue Entwickler und standardisiert Abläufe. Make wurde gewählt, weil es auf allen Unix-Systemen vorinstalliert ist und keine zusätzlichen Dependencies benötigt.

### Docker Compose

Die Containerisierung erfolgt über Docker Compose mit Umgebungs-spezifischen Konfigurationen:

| Datei                        | Zweck                     |
|------------------------------|---------------------------|
| `docker-compose.yml`         | Basis-Services (DB, Backend, Frontend, Prometheus) |
| `docker-compose.dev.yml`     | Development-Overrides      |
| `docker-compose.test.yml`    | Test-Umgebung mit isolierter DB |
| `docker-compose.staging.yml` | Staging-Deployment         |
| `docker-compose.prod.yml`    | Produktions-Deployment     |

### Pre-commit Hooks

Automatische Qualitätskontrollen vor jedem Commit:

| Hook                  | Zweck                                |
|-----------------------|--------------------------------------|
| `trailing-whitespace` | Entfernt überflüssige Leerzeichen     |
| `end-of-file-fixer`  | Stellt abschliessende Newlines sicher |
| `check-yaml`         | Validiert YAML-Syntax                 |
| `check-added-large-files` | Verhindert Dateien > 1 MB        |
| `check-merge-conflict` | Erkennt unaufgelöste Merge-Konflikte |
| `detect-private-key`  | Verhindert versehentliches Committen von Schlüsseln |
| `ruff`               | Python Linting mit Auto-Fix           |
| `black`              | Python Code-Formatierung              |

**Begründung**: Pre-commit Hooks implementieren das "Shift-Left"-Prinzip – Fehler werden bereits lokal erkannt, bevor sie in die CI-Pipeline gelangen. Das spart CI-Laufzeit und gibt dem Entwickler sofortiges Feedback.

→ Dateien: `Makefile`, `.pre-commit-config.yaml`, `docker-compose*.yml`

---

## LE3 — Continuous Integration

### CI-Pipeline (GitHub Actions)

Bei jedem Push auf `main`/`develop` und bei jedem Pull Request wird die CI-Pipeline ausgeführt:

```
┌─────────────────────────────────────────────┐
│              CI Pipeline                     │
│                                              │
│  Backend (Python)        Frontend (Next.js)  │
│  ├── Setup Python 3.12   ├── Setup Node 20   │
│  ├── pip install         ├── npm ci          │
│  ├── ruff lint           ├── eslint lint     │
│  └── pytest + coverage   └── next build      │
│                                              │
└─────────────────────────────────────────────┘
```

**Pipeline-Features**:
- **Concurrency Control**: `cancel-in-progress: true` — verhindert parallele Runs auf demselben Branch
- **Dependency Caching**: pip und npm Caches beschleunigen Builds
- **Code Coverage**: pytest-cov generiert Coverage-Reports als Build-Artefakte
- **Fail-Fast**: `-x` Flag stoppt Tests beim ersten Fehler

### CD-Pipeline (Continuous Deployment)

| Trigger          | Ziel       | Workflow                |
|------------------|------------|-------------------------|
| Push auf `develop` | Staging    | `deploy-staging.yml`    |
| Push auf `main`   | Production | `deploy-production.yml` |

Das Deployment erfolgt über SSH auf den Zielserver mit `scripts/deploy.sh`.

**Begründung**: Die Trennung von CI und CD in separate Workflows ermöglicht es, dass CI auf PRs läuft (schnelles Feedback), während CD nur auf Merges in geschützte Branches triggert. Das `cancel-in-progress` Feature spart Ressourcen bei schnell aufeinanderfolgenden Pushes.

→ Dateien: `.github/workflows/ci.yml`, `.github/workflows/deploy-staging.yml`, `.github/workflows/deploy-production.yml`

---

## LE4 — Software Testing

### Backend: pytest

Die Test-Suite umfasst folgende Bereiche:

| Test-Datei                | Getestete Komponente              |
|---------------------------|-----------------------------------|
| `test_auth_utils.py`      | JWT-Authentifizierung, Token-Validierung |
| `test_config.py`          | Konfigurationsvalidierung (Pydantic Settings) |
| `test_contact.py`         | Kontaktformular-API               |
| `test_gateway_remote.py`  | Gateway Remote Management         |
| `test_health.py`          | Health-Check Endpoint             |
| `test_macmini_stack.py`   | Mac Mini Stack Management         |
| `test_plant_evaluation.py`| Pflanzenbewertungs-Logik          |
| `test_public_observe.py`  | Öffentliche Beobachtungs-API      |

**Test-Konfiguration**:
- Marker `integration` separiert Docker-abhängige Tests
- `conftest.py` stellt gemeinsame Fixtures bereit
- CI führt nur Unit-Tests aus (`-m "not integration"`)

### Frontend: Jest + React Testing Library

- `jest.config.js` konfiguriert TypeScript-Support via `ts-jest`
- `jest.setup.ts` mockt Browser-APIs (z.B. IntersectionObserver)
- Coverage-Collection konfiguriert für `src/**/*.{ts,tsx}`

### Testbarkeit

Die Software ist testbar durch:
- **Dependency Injection**: FastAPI's `Depends()` ermöglicht das Ersetzen von Abhängigkeiten in Tests
- **Fixture-basiertes Setup**: `conftest.py` stellt DB-Sessions, Test-Clients und Mock-User bereit
- **Separation of Concerns**: Router → Service → Model Schichtung erlaubt isoliertes Testen

**Begründung**: Unit-Tests bilden die Basis der Testpyramide. Durch Marker können Integration-Tests lokal zugeschaltet, in der CI aber übersprungen werden. Die Kombination aus schnellen Unit-Tests (CI) und umfassenden Integration-Tests (lokal/staging) bietet einen guten Kompromiss zwischen Geschwindigkeit und Abdeckung.

→ Dateien: `backend/tests/`, `backend/conftest.py`, `frontend/jest.config.js`, `frontend/jest.setup.ts`

---

## LE5 — Clean Code

### Statische Code-Analyse & Linting

| Tool       | Sprache    | Konfiguration              | Zweck                    |
|------------|------------|----------------------------|--------------------------|
| **Ruff**   | Python     | `pyproject.toml`           | Linting (E, W, F, I, B, UP, S) |
| **Black**  | Python     | `pyproject.toml`           | Code-Formatierung         |
| **ESLint** | TypeScript | `.eslintrc.json`           | JavaScript/TypeScript Linting |
| **Prettier** | TypeScript | `.prettierrc`            | Code-Formatierung         |

**Ruff Regel-Kategorien**:
- `E/W`: PEP 8 Style
- `F`: Pyflakes (undefined names, unused imports)
- `I`: Import-Sortierung (isort)
- `B`: Bugbear (häufige Fehlerquellen)
- `UP`: Pyupgrade (moderne Python-Syntax)
- `S`: Bandit Security Checks

### Clean Code Prinzipien im Projekt

1. **Thin Routers**: Router/Controller enthalten nur Request-Parsing und Response-Mapping
2. **Service Layer**: Business-Logik in `app/services/`
3. **Model Layer**: Datenbankmodelle in `app/models/`
4. **Schema Validation**: Pydantic Schemas in `app/schemas/`
5. **Konfiguration via Environment**: Keine hardcodierten Werte (`app/config.py`)
6. **Security by Design**: JWT-Secret Validierung, CORS, Security Headers

### Coding Guidelines

- Python: Line length 100 (Black + Ruff)
- TypeScript: Prettier-Konfiguration in `.prettierrc`
- Imports: Automatisch sortiert durch `ruff` (isort-kompatibel)
- Pre-commit Hooks erzwingen Einhaltung automatisch

**Begründung**: Die Kombination aus Linter (Ruff) und Formatter (Black) eliminiert Style-Diskussionen im Code Review. Ruff wurde statt Flake8/pylint gewählt, weil es 10-100x schneller ist und die gleichen Regel-Sets unterstützt. Security-Regeln (Bandit/S) erkennen häufige Sicherheitsprobleme bereits zur Entwicklungszeit.

→ Dateien: `pyproject.toml`, `.pre-commit-config.yaml`, `.eslintrc.json`, `.prettierrc`

---

## LE6 — Metriken, Code Coverage und Logging

### Code Coverage

**Backend** (pytest-cov):
```bash
# Lokal mit HTML-Report
make test-cov
# → Report unter backend/htmlcov/index.html

# In der CI-Pipeline
pytest --cov=app --cov-report=term-missing --cov-report=xml
```

Konfiguration in `pyproject.toml`:
- Minimum Coverage: 40% (steigend)
- Source: `app/` (ohne `seed/` und `__pycache__/`)
- Report zeigt fehlende Zeilen

**Frontend** (Jest):
```bash
cd frontend && npm run test:coverage
```

### Metriken: Prometheus + FastAPI Instrumentator

GreenMind exponiert **Application Metrics** über den `/metrics`-Endpoint (Prometheus-Format):

```python
# main.py
from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app, endpoint="/metrics")
```

**Gesammelte Metriken**:

| Metrik                                    | Typ       | Beschreibung                        |
|------------------------------------------|-----------|-------------------------------------|
| `http_requests_total`                    | Counter   | Gesamtzahl HTTP-Requests            |
| `http_request_duration_seconds`          | Histogram | Request-Dauer in Sekunden           |
| `http_request_size_bytes`                | Summary   | Request-Grösse                      |
| `http_response_size_bytes`               | Summary   | Response-Grösse                     |
| `http_requests_in_progress`              | Gauge     | Aktive Requests                     |

**Prometheus** ist im Docker Compose Stack integriert und scraped den Backend-Service alle 5 Sekunden (`compose/prometheus.yml`).

**Begründung**: Prometheus wurde als Metriken-System gewählt, weil es der De-facto-Standard für Container-basierte Anwendungen ist. Die `prometheus_fastapi_instrumentator` Library instrumentiert automatisch alle Endpoints – kein manuelles Instrumentieren nötig. Die Metriken ermöglichen es, Performanceprobleme (langsame Endpoints), Lastmuster (Requests pro Sekunde) und Fehlerraten (5xx) zu erkennen.

### Logging: Strukturiertes Logging

GreenMind verwendet **strukturiertes Logging** im `key=value`-Format:

```
timestamp=2024-01-15T10:30:00Z level=INFO logger=app.routers.ingest msg="Data ingested" sensor_id=abc count=42
```

**Architektur** (`app/logging_config.py`):

| Komponente           | Beschreibung                                    |
|----------------------|-------------------------------------------------|
| `StructuredFormatter` | Custom Formatter für key=value Logformat        |
| `setup_logging()`    | Initialisierung beim App-Start                  |
| `get_logger(name)`   | Factory-Funktion für benannte Logger             |

**Log-Levels**:
- `DEBUG`: Detaillierte Diagnoseinformationen
- `INFO`: Normale Betriebsereignisse (Request-Logs, Startup)
- `WARNING`: Unerwartete Situationen die nicht kritisch sind
- `ERROR`: Fehler die behandelt werden
- `CRITICAL`: Systemausfälle

**Request Logging Middleware** (`main.py`):
```python
@app.middleware("http")
async def log_requests(request, call_next):
    # Loggt: Method, Path, Status Code, Duration (ms)
    # Überspringt Health-Checks um Log-Noise zu vermeiden
```

**Audit Logging** (`app/audit.py`):
- Persistentes Logging von Datenänderungen in der Datenbank
- Speichert: Benutzer, Entity-Typ, Aktion, Vorher/Nachher-Diff
- Ermöglicht Nachvollziehbarkeit von Änderungen (Compliance)

**Konfigurierbarkeit**:
- Log-Level über Environment-Variable `LOG_LEVEL` steuerbar
- Drittanbieter-Logger (uvicorn, sqlalchemy) auf WARNING gedrosselt

**Begründung**: Strukturiertes Logging (statt unstrukturiertem Text) ermöglicht maschinelle Auswertung durch Log-Aggregatoren (ELK, Loki). Das key=value Format wurde über JSON gewählt, weil es menschenlesbar UND maschinell parsebar ist. Die Request-Logging-Middleware loggt jede Anfrage mit Dauer – so können langsame Requests identifiziert werden. Health-Checks werden bewusst ausgefiltert, um das Log-Volumen zu reduzieren.

→ Dateien: `app/logging_config.py`, `app/main.py`, `app/audit.py`, `compose/prometheus.yml`, `pyproject.toml`

---

## Übersicht: Werkzeuge & Technologien

| Bereich                | Werkzeug/Technologie                    |
|------------------------|-----------------------------------------|
| Versionskontrolle      | Git, GitHub                             |
| Build-Tool             | Make, Docker Compose                    |
| CI/CD                  | GitHub Actions                          |
| Testing (Backend)      | pytest, pytest-cov, pytest-mock         |
| Testing (Frontend)     | Jest, React Testing Library             |
| Linting (Python)       | Ruff                                    |
| Formatting (Python)    | Black                                   |
| Linting (TypeScript)   | ESLint                                  |
| Formatting (TypeScript)| Prettier                                |
| Pre-commit             | pre-commit (trailing-ws, ruff, black)   |
| Metriken               | Prometheus, FastAPI Instrumentator      |
| Logging                | Python logging, StructuredFormatter     |
| Code Coverage          | pytest-cov, Jest --coverage             |
| Containerisierung      | Docker, Docker Compose                  |
| Deployment             | GitHub Actions → SSH → Docker Compose   |
