# Metriken – Übersicht, Einsatz und Grenzen

> Dieses Dokument beschreibt die eingesetzten Metriken in GreenMindDB,
> warum sie verwendet werden und wo ihre Grenzen liegen (SKO LE6).

---

## 1. Prometheus – Laufzeit-Metriken

### Was wird gemessen?

Der [`prometheus-fastapi-instrumentator`](https://github.com/trallnag/prometheus-fastapi-instrumentator) exponiert unter `/metrics` automatisch:

| Metrik | Typ | Beschreibung |
|--------|-----|-------------|
| `http_requests_total` | Counter | Gesamtzahl der HTTP-Anfragen (nach Method, Status, Handler) |
| `http_request_duration_seconds` | Histogram | Latenz-Verteilung pro Endpoint (p50, p90, p99) |
| `http_request_size_bytes` | Summary | Größe eingehender Requests |
| `http_response_size_bytes` | Summary | Größe ausgehender Responses |
| `http_requests_in_progress` | Gauge | Aktuell parallele Requests |

### Warum diese Metriken?

- **Latenz-Monitoring:** Bioelektrische Sensordaten werden nahezu in Echtzeit über den Ingest-Endpoint übertragen. Erhöhte Latenz bei `POST /api/v1/ingest` deutet auf Datenbankengpässe oder Netzwerkprobleme hin.
- **Error-Rate:** Ein Anstieg von 4xx/5xx-Fehlern zeigt Gateway-Authentifizierungsprobleme oder Schema-Validierungsfehler an.
- **Throughput:** Anzahl der Ingestions pro Zeiteinheit hilft bei der Kapazitätsplanung (z. B. wie viele Gateways parallel senden können).

### Grenzen

1. **Kein Business-Kontext:** Prometheus misst nur HTTP-Infrastruktur, nicht fachliche Metriken wie „Anzahl gestresster Pflanzen" oder „Datenqualität der Sensoren".
2. **Kein Alerting out-of-the-box:** Der `/metrics`-Endpoint exponiert Daten, aber ohne Alertmanager oder Grafana fehlt automatisierte Benachrichtigung bei SLA-Verletzungen.
3. **Single-Instance:** Im aktuellen Setup (Docker Compose, kein Kubernetes) werden Metriken nur von einer Backend-Instanz gesammelt. Bei horizontaler Skalierung bräuchte es einen gemeinsamen Prometheus-Server.
4. **Kein Request-Tracing:** Prometheus-Metriken liefern Aggregate, keine End-to-End-Traces. Für Request-Level-Debugging wäre OpenTelemetry nötig.

---

## 2. Coverage-Metriken

### Was wird gemessen?

- **Tool:** `pytest-cov` (basierend auf `coverage.py`)
- **Konfiguration:** `pyproject.toml` → `[tool.coverage.run]`
- **Mindest-Schwelle:** `--cov-fail-under=60` (CI bricht bei < 60 % ab)
- **Reports:** `term-missing` (Terminal-Ausgabe) + `xml:coverage.xml` (für CI-Artefakt)

### Warum diese Schwelle?

Die 60 %-Schwelle ist ein pragmatischer Kompromiss für ein R&D-Projekt:
- **40 %** war der vorherige Wert – zu niedrig, um Regressionssicherheit zu gewährleisten.
- **80 %+** wäre ideal, ist aber bei aktiver Feature-Entwicklung schwer durchzusetzen, besonders für Routers und Services mit externen Abhängigkeiten (MinIO, WebSocket, OTA).
- **60 %** (aktuell ~61 %) sichert die Kernlogik (Auth, Ingest, Zone CRUD) ab, ohne unrealistisch zu sein.

### CI-Integration

```yaml
# .github/workflows/ci.yml
- name: Run tests with coverage
  run: python -m pytest tests/ -v --cov=app --cov-report=xml:coverage.xml --cov-fail-under=60

- name: Upload coverage report
  uses: actions/upload-artifact@v4
  with:
    name: backend-coverage
    path: backend/coverage.xml
```

Der Coverage-Report wird als GitHub Actions Artefakt gespeichert und kann nach jedem CI-Lauf heruntergeladen werden.

### Grenzen

1. **Coverage ≠ Qualität:** 100 % Line-Coverage garantiert nicht, dass alle Grenzfälle getestet sind. Branch-Coverage oder Mutation-Testing wären genauer, aber aufwändiger.
2. **Keine Produktionsdaten-Abdeckung:** Tests laufen gegen SQLite mit synthetischen Daten. Edge-Cases, die nur mit realen Sensor-Datenströmen auftreten (z. B. NaN-Werte, Zeitstempel-Sprünge), werden nicht abgedeckt.
3. **Ausschlüsse:** `app/seed/*` und `__pycache__` werden aus gutem Grund exkludiert, aber auch Alembic-Migrationen und WebSocket-Handler sind schwer testbar und drücken die Coverage.

---

## 3. CI-Laufzeit

### Was wird gemessen?

- **Plattform:** GitHub Actions (Ubuntu-latest Runner)
- **Messung:** Automatische Zeitangabe pro Job und Step in der Actions-UI
- **Aktuelle Jobs:** `backend` (Python: Lint + Tests) und `frontend` (Node: Lint + Build + Tests)

### Warum CI-Laufzeit beobachten?

- **Feedback-Geschwindigkeit:** Ein CI-Lauf unter 3 Minuten ermöglicht schnelle Iterationszyklen. Wenn die Laufzeit auf > 10 Minuten steigt, blockiert CI den Entwicklungsfluss.
- **Kostenrelevanz:** GitHub Actions-Minuten sind für private Repos begrenzt. Unnötig lange Builds kosten Budget.
- **Frühwarnung:** Steigende Testlaufzeiten können auf Performance-Probleme in der Testsuite hinweisen (z. B. fehlende Mocks, echte Netzwerk-Calls).

### Grenzen

1. **Kein Trend-Tracking:** GitHub Actions bietet keine eingebaute Laufzeit-Trend-Analyse. Für langfristige Beobachtung bräuchte es ein externes Tool (z. B. Datadog CI Visibility, BuildPulse).
2. **Runner-Varianz:** GitHub-Hosted Runner haben keine garantierte Performance. Laufzeiten können je nach Last variieren (±30 %).
3. **Keine Parallelisierung:** Die aktuelle CI hat zwei parallele Jobs (backend/frontend), aber innerhalb eines Jobs laufen Tests sequentiell. Bei wachsender Testsuite wäre `pytest-xdist` eine Option.

---

## Zusammenfassung

| Metrik-Kategorie | Tool | Schwelle/Ziel | Hauptlimitation |
|-----------------|------|---------------|-----------------|
| **Laufzeit-Monitoring** | Prometheus | Keine SLA definiert | Kein Alerting, kein Business-Kontext |
| **Code-Coverage** | pytest-cov | ≥ 60 % | Coverage ≠ Qualität, keine Produktionsdaten |
| **CI-Laufzeit** | GitHub Actions | < 5 min angestrebt | Kein Trend-Tracking, Runner-Varianz |
