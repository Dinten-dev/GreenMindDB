# Testing – Übersicht und Anleitung

> Dieses Dokument beschreibt die Testinfrastruktur in GreenMindDB,
> wie Tests lokal ausgeführt werden und welche Bereiche abgedeckt sind.

---

## 1. Backend (pytest)

### Voraussetzungen

```bash
cd backend
source .venv/bin/activate     # virtualenv aktivieren
pip install pytest pytest-mock pytest-cov  # falls noch nicht installiert
```

### Tests ausführen

```bash
# Alle Unit Tests (ohne Docker-Integration)
SKIP_DOCKER_TESTS=1 \
JWT_SECRET_KEY="ci-test-secret-key-that-is-at-least-32-chars" \
FIRMWARE_STORAGE_DIR=/tmp/gm_firmware_test \
python -m pytest tests/ -v --tb=short -m "not integration"

# Mit Coverage-Report
python -m pytest tests/ -v --tb=short -m "not integration" \
  --cov=app --cov-report=term-missing --cov-fail-under=60

# Einzelne Testdatei
python -m pytest tests/test_boundary_analysis.py -v

# Nur ein spezifischer Test
python -m pytest tests/test_auth_router.py::TestLogin::test_login_success -v
```

### Umgebungsvariablen

| Variable | Pflicht | Beschreibung |
|----------|---------|-------------|
| `JWT_SECRET_KEY` | ✅ | Mind. 32 Zeichen, beliebiger Wert für Tests |
| `SKIP_DOCKER_TESTS` | ✅ | `"1"` um Docker-basierte Integration-Tests zu überspringen |
| `FIRMWARE_STORAGE_DIR` | ✅ | Temp-Verzeichnis für Firmware-Uploads (z. B. `/tmp/gm_firmware_test`) |

### Test-Fixtures (conftest.py)

Die zentrale `conftest.py` stellt folgende Fixtures bereit:

| Fixture | Scope | Beschreibung |
|---------|-------|-------------|
| `_reset_tables` | autouse | Erstellt alle SQLite-Tabellen vor jedem Test, löscht nach |
| `db` | function | Saubere SQLAlchemy-Session gegen In-Memory SQLite |
| `client` | function | `TestClient` mit überschriebener DB-Dependency |
| `admin_token` | function | JWT-Token für einen Admin-User (erstellt User + Login) |
| `setup_test_data` | function | Seed-Daten: Organization, Zone, Gateway, Sensor |

> **Wichtig:** Tests laufen gegen SQLite (nicht PostgreSQL). SQLite-spezifische
> Adapter für UUID- und `now()`-Funktionen werden in `conftest.py` registriert.
> TimescaleDB-Hypertables (z. B. `sensor_reading`) verhalten sich wie normale
> Tabellen in SQLite.

### Testdateien und Abdeckung

| Datei | Bereich | Tests |
|-------|---------|-------|
| `test_auth_utils.py` | JWT + Password-Hashing | 9 |
| `test_auth_router.py` | Auth-Endpoints (signup/login/logout/me) | 13 |
| `test_boundary_analysis.py` | Ingest + Zone + Auth Grenzwerte | 11 |
| `test_contact.py` | Kontaktformular + Honeypot | 3 |
| `test_config.py` | Settings-Validierung | 7 |
| `test_gateway_admin.py` | Fleet Overview + Audit Logs | 8 |
| `test_health.py` | Health + Root + Security Headers | 6 |

### Lint

```bash
python -m ruff check app/ tests/
```

---

## 2. Frontend (Jest)

### Voraussetzungen

```bash
cd frontend
npm ci
```

### Tests ausführen

```bash
# Alle Tests
npx jest --ci --passWithNoTests

# Mit Coverage
npx jest --coverage

# Nur Component-Tests
npx jest src/components/__tests__/

# Watch-Modus (Entwicklung)
npx jest --watch
```

### Test-Setup (jest.setup.ts)

Das Setup registriert globale Mocks für APIs, die in jsdom nicht existieren:

- **`IntersectionObserver`** – benötigt von `ScrollReveal`-Komponente
- **`window.matchMedia`** – benötigt von responsiven Komponenten

### Testdateien

| Datei | Komponente | Tests |
|-------|-----------|-------|
| `Footer.test.tsx` | Footer | 2 (Copyright, Links) |
| `Modal.test.tsx` | Modal | 4 (open/close, Escape, Backdrop) |
| `ContactPage.test.tsx` | Kontaktformular | 5 (Render, Submit, Error, Honeypot, Reset) |
| `EarlyAccessPage.test.tsx` | Early Access Form | 4 (Render, Submit, Error, Honeypot) |

### jest.config.js

- **Transform:** `ts-jest` mit `{ jsx: 'react-jsx' }` (da `tsconfig.json` `preserve` nutzt)
- **CSS-Stub:** `.css`-Imports werden gemockt
- **Path-Alias:** `@/` → `src/`

---

## 3. CI-Pipeline

Die CI-Pipeline (`.github/workflows/ci.yml`) läuft bei jedem Push und PR auf `main`/`develop`:

### Backend-Job

1. Python 3.12 Setup
2. `pip install` Dependencies + Test-Tools
3. `ruff check` (Lint)
4. `pytest` mit `--cov-fail-under=60` (Coverage-Gate)
5. Coverage-XML als Artefakt hochladen
6. Dynamischer Coverage-Badge (nur auf `main`)

### Frontend-Job

1. Node.js 20 Setup
2. `npm ci`
3. `npm run lint` (ESLint)
4. `npm run build` (Next.js Build)
5. `npx jest --ci --coverage`
6. Coverage-Ordner als Artefakt hochladen

### Coverage-Badge Setup

Der dynamische Coverage-Badge benötigt zwei GitHub-Konfigurationen:

1. **Secret `GIST_TOKEN`**: GitHub Personal Access Token mit `gist`-Scope
2. **Variable `COVERAGE_GIST_ID`**: ID eines GitHub Gists für die Badge-Daten

Bis diese konfiguriert sind, zeigt der statische Badge im README den Mindestwert an.

---

## 4. Teststrategie

### Grenzwertanalyse

Alle Boundary-Tests in `test_boundary_analysis.py` dokumentieren explizit
die getesteten Grenzwerte als Kommentare:

```python
def test_ingest_empty_readings_list(...):
    """Boundary: readings list is empty (length = 0).
    Zero readings is a valid edge case...
    """
    payload = {
        "readings": [],  # Grenzwert: leere Liste
    }
```

### Was NICHT getestet wird (und warum)

| Bereich | Grund |
|---------|-------|
| WebSocket-Handler (`ws.py`) | Erfordert echte WebSocket-Verbindung, zu komplex für Unit Tests |
| MinIO/S3 Uploads (`firmware_service.py`) | Externe Abhängigkeit, braucht Mocking oder Integration-Test |
| TimescaleDB-spezifische Features | SQLite hat keine Hypertables; Aggregations-Queries weichen ab |
| OTA-Update-Delivery | Gateway-seitig, nicht im Backend testbar |

### Coverage-Ziele

| Bereich | Aktuell | Ziel |
|---------|---------|------|
| Gesamt | ~65 % | 70 %+ |
| Auth (`auth.py`, `auth` Router) | ~80 % | 90 % |
| Ingest (`ingest.py`, `ingest_service.py`) | ~85 % | 90 % |
| Zone CRUD | ~80 % | 85 % |
| Gateway Admin | ~50 % | 60 % |
