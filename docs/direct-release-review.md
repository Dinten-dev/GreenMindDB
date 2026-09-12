# GreenMind: lokale Änderungsdurchsicht für develop / Staging

Stand: 12. September 2026. **Gemeinsame Basis main + Direct für develop/Staging freigegeben.**
Der Benutzer hat ausdrücklich angewiesen, die bestehenden main-Änderungen
ebenfalls zu prüfen und anschliessend beide Änderungen auf develop zu pushen.
Dieser Bericht beschreibt den geprüften Stand und die nachstehend dokumentierte
Staging-Nachbesserung. Eine Freigabe für Produktion oder Firmware-Flash besteht
weiterhin nicht.

## Staging-Auslieferung: Build vom gemeinsamen Server verlagert

Die erste gemeinsame Lieferung `305cb5a` hat alle drei GitHub-CI-Jobs bestanden.
Der anschliessende Staging-Lauf `34697116124` wurde abgebrochen: Sein bisheriger
serverseitiger Build beanspruchte den gemeinsamen 4-GB-Host stark. Beim Check
waren zeitweise nur 51 MB RAM verfügbar, ohne Swap; das ist kein tragfähiger
Deploymentweg für einen gleichzeitig Daten empfangenden Produktionsserver.
Der eindeutig zugehörige Staging-Buildprozess wurde beendet. Bestehende
Produktionscontainer wurden weder neu gestartet noch ersetzt; beim folgenden
Statuscheck meldeten alle vorhandenen Healthchecks wieder gesund.

`scripts/deploy.sh` baut deshalb ausschliesslich für Staging Backend und
Frontend nacheinander auf dem aufrufenden GitHub-Runner. Die Zielarchitektur
wird über die bereits verifizierte SSH-Verbindung gelesen. Fertige Images
werden über dieselbe Verbindung gestreamt und geladen; erst nach erfolgreichem
Transfer startet `gm-staging` mit `--no-build`. Auch `--skip-build` darf auf
Staging keinen Serverbuild auslösen. Fehler beim Build, Export oder Laden
brechen vor dem Containerwechsel ab. Produktionsbefehle bleiben unverändert.
Zehn Tests führen das echte Deploymentskript mit ersetzten externen Befehlen
aus und prüfen Zielarchitektur, Reihenfolge, Abbruchverhalten sowie den
unveränderten Produktionszweig. Die tatsächliche Auslieferung und Serverreserve
müssen zusätzlich anhand des neuen Staging-Laufs kontrolliert werden.

## Entschiedener Umfang und Ergebnis der Nachprüfung

1. **Staging-Ausgangsstand:** Der gelesene Backend-Branch `develop` (`93cea4e`)
   liegt einen bestehenden Commit hinter `main` (`3c82579`). Dieser Commit
   umfasst **52 Dateien, +3.762 / −112 Zeilen**. Die neue Direct-Implementierung
   verwendet dessen vorhandene WAV-Merkmalsberechnung. Der Benutzer hat den
   gemeinsamen Abgleich mit main gewählt. Alle 52 Dateien wurden nachgeprüft:
   alte API-Pflichtfelder bleiben gültig, zusätzliche Felder sind optional oder
   haben rückwärtskompatible Datenbankdefaults; alte Rohpfade bleiben erhalten.
   Die unten gelisteten 52 Dateien sind keine neu geschriebenen Direct-Dateien.
2. **Sicherheitsbefund behoben:** Next.js und eslint-config-next wurden gezielt
   von 16.3.1 auf 16.3.5 aktualisiert, Sharp mit den zugehörigen Binärpaketen
   von 0.35.3 auf 0.35.4. Sonstige Paketfamilien wurden nicht angehoben.
   Der Produktionsabhängigkeitscheck meldet jetzt null Schwachstellen;
   17 Oberflächentests, Build, Typprüfung, Lint und Formatierung bestehen.
3. **Firmware-Zielbranch:** Remote existiert noch kein `develop`. Die lokale
   Lieferkopie basiert auf veröffentlichtem `main` (`773b958`); sie enthält
   ausschliesslich sechs zusätzliche Dateien. Die persönliche Captive-Portal-
   Entwicklung wird nicht stillschweigend mitgeliefert.

Der Push enthält die bereits bestehende Erweiterung, Direct und die folgenden
gezielten Korrekturen/Nachweise. Die tatsächlich laufende Serverversion wird
nicht aus main abgeleitet; die Staging-Auslieferung wird anhand ihres eigenen
GitHub-Laufs kontrolliert.

| Zusätzliche Dateiänderung gegenüber dem ersten Review | Bereich / Grund |
|---|---|
| `frontend/package.json` | SHARED: gezielte Next.js-/ESLint-Sicherheitspatches. |
| `frontend/package-lock.json` | SHARED: exakt aufgelöste Patchversionen, einschliesslich Sharp/libvips. |
| `backend/app/services/wav_service.py` | LEGACY-Archivprüfung: gespeicherte Ableitungen zurücklesen und tatsächliche Bytes hashen; Metadaten allein reichen nicht. Bestehende Uploadannahme unverändert. |
| `backend/tests/test_wav_features.py` | LEGACY: korrekte sowie beschädigte Archivbytes trotz korrekter Metadaten prüfen. |
| `backend/tests/test_staging_migrations.py` | SHARED: echte TimescaleDB 2.17.2/PG15, Upgrade 0019→0022 mit Altdaten und Nachlieferung in komprimierte Chunks. Nur leere lokale Testdatenbank erlaubt. |
| `.github/workflows/ci.yml` | SHARED: Migrationstest zusätzlich zum Direct-Konkurrenztest verpflichtend ausführen. |
| `docker-compose.staging.yml` | SHARED: Retention für den Sensorpilot ausdrücklich aus und Dry-Run an; alte Servervariablen können keine Löschung aktivieren. Produktion unverändert. |

Die älteren Migrationen 0017 und 0019 ändern ausschliesslich Formatierung;
ihre Funktionslogik wurde zusätzlich über den Syntaxbaum verglichen. Die
bereits vorhandene Backend-Lockdatei ergänzt nur greenlet und numpy; sie
enthält keine weiteren Versionssprünge bestehender Pakete. Retention und
optionale Archivierung werden durch die Staging-Auslieferung nicht aktiviert.

## Datenwege und Kompatibilitätsgrenze

```text
ALT:    unveränderte Sensoren → unveränderter Pi
        → bisherige Messwert- und WAV-Endpunkte
        → bisherige Tabellen, Objekte, Analyse und Retention

DIRECT: explizit eingerichteter neuer Sensor → eigener HTTPS-Endpunkt
        → eigene Datenbank: Payload + Metadaten verbindlich speichern
        → eigener Worker → PCM16/PCM24-WAVs + Merkmale im eigenen Bucket

DUAL:   unterstützter Testsensor → Pi als bisherige Analysequelle
                               → Direct als getrennte Vergleichsdaten
```

Die neue Erweiterung verändert gegenüber `3c82579` keine bestehende Laufzeit-
API, kein altes Konfigurationsdefault, keinen Legacy-Uploadvalidator, keine
Legacy-Tabelle, keinen alten Schlüssel-Fallback, keinen Gateway-WAV-Pfad und
keine alte Retention. Die gesonderten Nachprüfungskorrekturen oben verstärken
die Archivverifikation, aktualisieren Sicherheitsabhängigkeiten und erweitern CI.
Die frühere 52-Dateien-Differenz gegenüber develop muss unabhängig hiervon
bewertet werden; insbesondere verändert sie bereits bestehende Laufzeitdateien.

Direct ist standardmässig deaktiviert und wird vom bisherigen Deployment nicht
gestartet. Eigene Prozesse, Datenbank, Zugangsdaten, Budgets und Objektpfade
begrenzen gegenseitige Abhängigkeiten. Gemeinsame Hardware oder voller gemeinsamer
Plattenspeicher bleiben ein mögliches gemeinsames Ausfallrisiko; reale Quoten und
Speicherreserven sind vor dem Pilotbetrieb zu prüfen.

DUAL ist auf Mono/380 Hz/PCM16 beschränkt, weil der bestehende Gateway diesen
Vertrag tatsächlich unterstützt. Seine Direct-Daten gelangen weder in alte
Dashboard-Aggregate noch in alte WAV-Tabellen. Vierkanal/500-Hz/PCM24 funktioniert
im Direct-Protokoll und Assembler; es wird nicht verlustbehaftet für DUAL
umgewandelt. Eine gemeinsame Dashboard-Darstellung ist nicht implementiert.

## Jede neue oder geänderte Backend-Datei

Pfadangaben beziehen sich auf `GreenMindDB`. DIRECT betrifft ausschliesslich
den neuen Weg; SHARED betrifft Prüfungen oder eine später aktivierte gemeinsame
Einrichtung. Die aktuelle Erweiterung hat keine LEGACY-Laufzeitänderung.

| Datei | Aktion / Bereich | Zweck |
|---|---|---|
| `.github/workflows/ci.yml` | geändert / SHARED | Zusätzlicher PostgreSQL-Job prüft Konkurrenz und Transaktionen; keine Deployment-Aktion ergänzt. |
| `backend/app/direct/__init__.py` | neu / DIRECT | Getrenntes Modul, kein Import in der bisherigen App. |
| `backend/app/direct/config.py` | neu / DIRECT | Eigene Defaults, TLS, passende Umgebungs-Buckets, Grössen- und Retentionsgrenzen. |
| `backend/app/direct/database.py` | neu / DIRECT | Begrenzte PostgreSQL-Verbindungen, Transaktionen und dauerhafte Commits. |
| `backend/app/direct/models.py` | neu / DIRECT | Eigene Geräte, Sitzungen, Chunks, Stücke, Segmente, Revisionen, Budgets und Heartbeats. |
| `backend/app/direct/protocol.py` | neu / DIRECT | Binärvertrag, Prüfsummen, Session-Konsistenz und deterministische Zeitgrenzen. |
| `backend/app/direct/auth.py` | neu / DIRECT | Separate gerätegebundene Schlüssel und Aktivitätsprüfung. |
| `backend/app/direct/ingest.py` | neu / DIRECT | Atomare Annahme, Quoten, Deduplizierung und konkurrierende Uploads. |
| `backend/app/direct/main.py` | neu / DIRECT | Eigene API, Body-/Zeitlimits, ACK, gerätebezogene WAV-Abfrage und Health. |
| `backend/app/direct/storage.py` | neu / DIRECT | Lokale Testablage beziehungsweise eigener S3-Bucket; Lesen/Prüfen vor Veröffentlichung. |
| `backend/app/direct/features.py` | neu / DIRECT | Verlustfreie PCM24-Leseschicht; bisherige Merkmalsrechnung je Kanal/zusammenhängendem Abschnitt. |
| `backend/app/direct/assembler.py` | neu / DIRECT | Gesperrte Segmentverarbeitung, Lückenmanifest, WAV-Revisionen und geprüfte Chunk-Freigabe. |
| `backend/app/direct/worker.py` | neu / DIRECT | Separater Worker mit Wiederholung, Heartbeat und eigenem Retentionslauf. |
| `backend/app/direct/retention.py` | neu / DIRECT | Standardmässig aus/Dry-Run; nur geprüfte Direct-Objekte und Metadaten löschen. |
| `backend/app/direct/provision.py` | neu / DIRECT | Explizite Schemaerstellung, echte Eigentumsprüfung, Token-Datei und Deaktivierung. |
| `backend/tests/__init__.py` | neu / SHARED | Eindeutiger Import des lokalen Testpakets. |
| `backend/tests/direct/__init__.py` | neu / DIRECT | Separates Testpaket. |
| `backend/tests/direct/conftest.py` | neu / DIRECT | Isolierte Geräte-, Datenbank- und API-Testumgebung. |
| `backend/tests/direct/test_pipeline.py` | neu / DIRECT | Protokoll, PCM, Fehler, Konkurrenz, Quoten, Lücken, Wiederaufnahme und Isolation. |
| `backend/tests/direct/test_postgres.py` | neu / DIRECT | Echte PostgreSQL-Sperren, konkurrierende Anfragen/Worker und optionaler DB-Neustart. |
| `backend/tests/direct/test_s3.py` | neu / DIRECT | Echter lokaler MinIO-Rundlauf einschliesslich geprüfter Löschung. |
| `backend/tests/direct/test_gateway_comparison.py` | neu / SHARED | C++-Kodierung gegen unveränderten Gateway-Writer und Direct-WAV vergleichen. |
| `backend/tests/test_legacy_direct_compatibility.py` | neu / SHARED | 91 Tage alte WAVs und Messwerte bei Direct-Speichergrenze sowie abgeschaltetem Direct. |
| `docker-compose.direct.yml` | neu / DIRECT | Opt-in-Overlay mit eigenen Diensten, Ressourcenlimits und Datenbank-Volume. |
| `deploy/nginx/direct-ingest.conf.example` | neu / SHARED bei Aktivierung | Nur zusätzlichen Staging-URL-Pfad zum neuen Dienst routen. |
| `scripts/direct_simulator.py` | neu / DIRECT | Expliziter synthetischer HTTPS-Upload mit Identitäts-/ACK-Prüfung. |
| `docs/direct-to-cloud.md` | neu / DIRECT / SHARED | API, Einrichtung, Datenformate, Grenzen, Staging-Schritte und Rückfall. |
| `docs/direct-release-review.md` | neu / SHARED | Diese vollständige Durchsicht und ihre Nachweise. |

Neue API: `/api/v1/direct-ingest/chunks`, gerätebezogene Segmentliste und
WAV-Downloads. Bestehende Endpunkte bleiben auf ihren bisherigen Prozessen.
Direct erzeugt initial nur eigene `direct_*`-Tabellen in einer getrennten
Datenbank. Es gibt keine neue Legacy-Alembic-Migration. Die explizite initiale
Schemaerstellung ist kein automatischer Versionsmigrationsmechanismus; spätere
Schemaänderungen benötigen eigene versionierte Migrationen.

## Jede neue Firmware-Datei

Pfadangaben beziehen sich auf `GreenMindArdu`, Basis `773b958`. Keine bestehende
Firmware-Datei wird geändert. Das Gateway-Repository bleibt vollständig sauber.

| Datei | Aktion / Bereich | Zweck |
|---|---|---|
| `direct_transport/DirectCore.h` | neu / DIRECT | Kopierte Sampleblöcke, PCM16/24 und getrennte begrenzte Queues. |
| `examples/direct_sensor/platformio.ini` | neu / DIRECT | Eigenständiges ESP32-S3-Testziel; kein automatisches Flashen. |
| `examples/direct_sensor/src/main.cpp` | neu / DIRECT | AD8232-Testaufnahme, GATEWAY/DIRECT/DUAL, TLS, ACK, Retry, NVS-Provisionierung und Drop-Zähler. |
| `examples/direct_sensor/README.md` | neu / DIRECT | Konfiguration, Testgrenzen und klare Abgrenzung zur eingesetzten Firmware. |
| `tests/direct_transport/test_core.cpp` | neu / DIRECT | Modusdefault, PCM-Grenzen, Queue-Ownership, Überlauf und 10.000 konkurrierende Blöcke. |
| `.github/workflows/direct-tests.yml` | neu / SHARED | Separater Hosttest und ESP32-Build, ohne Flash-/OTA-Schritt. |

Das Testziel verwendet eine eigene grosse App-Partition und bietet kein OTA.
Es ist kein Ersatz für die eingesetzte Firmware mit deren bisherigen Funktionen.
Die reale ADS131M04-Aufnahme und deren Treiber sind nicht implementiert.
RAM puffert normalerweise etwa fünf Sekunden je Weg; danach werden neue
ausgelassene Samples ausdrücklich gezählt. Aufnahme blockiert nicht auf Uploads.
RAM überlebt keinen Reset; sieben Tage SD-Persistenz sind noch nicht vorhanden.

## Bereits bestehende 52 Dateien zwischen develop und main

Diese gesamte Tabelle gehört zum vom Benutzer gewählten Staging-Abgleich.
Sie ist gegenüber dem neuen Direct-Diff separat zu lesen. Die Migrationen
0020–0022 wurden lokal auf derselben TimescaleDB-/PostgreSQL-Version geprüft;
die reale Staging-Ausführung folgt erst im autorisierten Deployment.

| Datei | Bereits vorhandene Änderung / Bedeutung für die Durchsicht |
|---|---|
| `.env.example` | Neue Feature-, Retentions- und Betriebsparameter. |
| `.env.production.example` | Entsprechende Produktionsvorlage; keine Produktionsanwendung erfolgt. |
| `.env.staging.example` | Entsprechende Staging-Vorlage. |
| `backend/Dockerfile` | Backend-Image/Startkonfiguration und benötigte Verarbeitung. |
| `backend/alembic/versions/0017_provisioning_jobs.py` | Bereits vorhandene Formatierungsänderung einer älteren Migration. |
| `backend/alembic/versions/0019_sensor_sms_alerts.py` | Bereits vorhandene Formatierungsänderung einer älteren Migration. |
| `backend/alembic/versions/0020_wav_health_and_source_metadata.py` | Zusätzliche WAV- und Quellmetadaten. |
| `backend/alembic/versions/0021_verified_wav_features.py` | Geprüfte WAV-Merkmale und Archivmetadaten. |
| `backend/alembic/versions/0022_optimized_pipeline_and_operations.py` | Aggregate, Feature-Versionen, Betriebs-/Retentionstabellen und konkurrierender BRIN-Indexaufbau. |
| `backend/app/config.py` | Feature- und Retentionsdefaults; Retention aus/Dry-Run, Merkmalsrechnung an. |
| `backend/app/main.py` | Betriebszustand und Hintergrundverarbeitung. |
| `backend/app/models/__init__.py` | Zusätzliche Modelle registrieren. |
| `backend/app/models/master.py` | Zusätzliche Gateway-/Quelldaten. |
| `backend/app/models/operations.py` | Retentionslauf und Workerzustand. |
| `backend/app/models/timeseries.py` | Optionale Aggregate und Qualitäts-/Quellfelder. |
| `backend/app/models/wav_file.py` | WAV-Metadaten, Merkmale, Versionierung und Archivzustand. |
| `backend/app/operational_metrics.py` | Betriebs- und Speichermetriken. |
| `backend/app/routers/gateways.py` | Ergänzte Gateway-Auskunft. |
| `backend/app/routers/wav.py` | Zusätzliche Metadaten, Health und Merkmalsintegration im alten WAV-Pfad. |
| `backend/app/schemas/gateway.py` | Ergänzte optionale Gateway-Felder. |
| `backend/app/schemas/ingest.py` | Optionale Aggregate/Qualität und deren Validierung. |
| `backend/app/services/gateway_service.py` | Übernahme zusätzlicher Gateway-Daten. |
| `backend/app/services/ingest_service.py` | Aggregate speichern und bestehende Messwerte deduplizieren. |
| `backend/app/services/retention_service.py` | Gesteuerte geprüfte Legacy-Retention. |
| `backend/app/services/wav_feature_service.py` | Vorhandene numerische Merkmalsrechnung, die Direct wiederverwendet. |
| `backend/app/services/wav_service.py` | WAV-Metadaten und Ablage/Verifikation. |
| `backend/app/workers/__init__.py` | Hintergrundworker-Paket. |
| `backend/app/workers/common.py` | Gemeinsame Worker-Hilfen. |
| `backend/app/workers/retention.py` | Separater alter Retentionsworker. |
| `backend/app/workers/wav_features.py` | Separater alter WAV-Feature-Worker. |
| `backend/requirements.lock` | Aufgelöste Abhängigkeiten der vorherigen Erweiterung. |
| `backend/requirements.txt` | Zusätzliche numerische/Worker-Abhängigkeiten. |
| `backend/tests/conftest.py` | Angepasste Test-Fixtures. |
| `backend/tests/fixtures/gateway-release-signing-public.pem` | Öffentlicher Testschlüssel. |
| `backend/tests/test_boundary_analysis.py` | Zusätzliche Grenzprüfungen. |
| `backend/tests/test_gateway_remote.py` | Aktualisierte Gateway-Prüfungen. |
| `backend/tests/test_security_hardening.py` | Zusätzliche Sicherheits-/Kompatibilitätsprüfungen. |
| `backend/tests/test_wav_features.py` | Vorhandene Feature-/Retentionstests. |
| `backend/tests/test_wav_timing.py` | Vorhandene Timing-/Metadatentests. |
| `compose/.env.example` | Parameter für den lokalen Stack. |
| `compose/.env.test` | Testumgebung für zusätzliche Funktionen. |
| `compose/prometheus-rules.yml` | Betriebswarnungen. |
| `compose/prometheus.yml` | Zusätzliche Metrikerfassung. |
| `docker-compose.prod.yml` | Bestehende Worker-/Ressourcenänderungen für Produktion. |
| `docker-compose.staging.yml` | Bestehende Worker-/Ressourcenänderungen für Staging. |
| `docker-compose.yml` | Entsprechende lokale Dienste. |
| `frontend/Dockerfile` | Bestehende Image-Anpassung. |
| `frontend/src/app/[locale]/app/dashboard/page.tsx` | Bestehende Anzeige-/Aggregatanpassung. |
| `frontend/src/app/[locale]/app/gateways/page.tsx` | Gateway-Zustandsanzeige. |
| `frontend/src/app/[locale]/app/sensors/page.tsx` | Ergänzte Sensoranzeige. |
| `frontend/src/lib/api.ts` | Zusätzliche API-Datentypen und Zugriffe. |
| `scripts/deploy.sh` | SSH-Verbindungserhalt, zusätzliche Ausschlüsse und geänderter Produktions-Nginx-Zielname. |

Eine Rücknahme dieser früheren Migrationen würde Spalten/Tabellen entfernen.
Sie gehört ausdrücklich nicht zum Direct-Rückfallplan. Vor einem Staging-Abgleich
sind dessen aktuelles Schema, Backup und tatsächliche Migrationsfolge zu prüfen.

## Ausgeführte lokale Prüfungen

Protokolle liegen ausserhalb der Liefer-Repositories unter `implementation/validation`.
Tests mit Mock-Objektspeicher sind kein Nachweis eines realen Server-Uploads.

| Prüfung | Ergebnis und Aussagegrenze |
|---|---|
| Backend vor Erweiterung | PASS: 175 Tests; 10 Docker-Integrationstests ausgenommen. |
| Backend mit Direct | PASS: final 201 Tests; 68,03 % Coverage; 17 Integrationstests separat ausgenommen. |
| Direct-Konfigurations-/Pipelinetests | PASS: 22; gültige Staging-Einstellungen und Ablehnung falscher Bucket-Umgebung, fehlender Schlüssel, ungesichertem Speicher und falscher DB. |
| Beide alten Uploadformate | PASS: 2 Fälle; 91 Tage alte WAVs/Messwerte während Direct 503 meldet, Wiederholung sowie Abschalten von Direct. WAV-Objektablage hier gemockt. |
| Echtes PostgreSQL + MinIO | PASS: 5 Integrationstests; 30 konkurrierende identische Anfragen ergeben genau einen Chunk, zwei Worker eine Revision, zwei Geräte 40 eigene Chunks. |
| Bestätigung und DB-Neustart | PASS: bestätigter Chunk nach Neustart des ausschliesslich lokalen PostgreSQL-Containers vorhanden; identische Wiederholung als Duplikat bestätigt. Kein Stromausfalltest. |
| Echtes MinIO | PASS: Objekt hochladen, zurücklesen/verifizieren, nach freigegebener Retention löschen. Lokaler Entwicklungsendpunkt; kein Staging-TLS-Nachweis. |
| Unveränderter Gateway | PASS: 138 Tests. |
| Vergleich Gateway/Direct/C++ | PASS: alle 33.001 Werte von 0 bis 3.300 mV in 0,1-mV-Schritten erzeugen identische PCM16-Samples. |
| PCM24 und Zeitfenster | PASS: Vorzeichen-/Grenz-/Low-Bit-Fälle, Kanalreihenfolge, kompletter 10-Minuten-Block bei 500 Hz/vier Kanälen, UTC-Grenze, verspätete Ergänzung und Lücken. |
| C++-Hosttest | PASS: PCM16/24, Modusdefault, kopierte Queue-Daten, Überlauf und 10.000 konkurrierende Blöcke. |
| Bestehende Firmware | PASS: 12 Sicherheitsinvarianten und Build des veröffentlichten main-Standes. |
| Neue Testfirmware | PASS: separater ESP32-S3-Build; RAM 137.516/327.680 Byte, Flash 985.977/3.145.728 Byte. Kein Hardwarelauf. |
| Oberfläche | PASS: 17 Tests, Lint, Formatierung, Typprüfung und Produktionsbuild nach den Sicherheitspatches. |
| Backend-Stil | PASS: Ruff und Formatprüfung einschliesslich des neuen Migrationstests. |
| Compose | PASS: Konfiguration mit ausschliesslich künstlichen Werten validiert; neue Dienstnamen überschreiben keine alten Dienste. |
| Backend-Abhängigkeiten | PASS: pip-audit meldet keine bekannten Schwachstellen. |
| Frontend-Abhängigkeiten | PASS: npm audit meldet nach den gezielten Patchversionen null Schwachstellen. |
| Vorherige Legacy-Integrationstests | PASS: neun Tests im isolierten Docker-Stack plus der separat ausgeführte zehnte echte MinIO-/FLAC-Test. |
| Migration mit vorhandenen Altdaten | PASS: 0019→0022 erhält einen 91 Tage alten Messwert und WAV-Verweis; weiterer alter Upload in komprimiertem Timescale-Chunk und Wiederholung erfolgreich. |
| Git-/Quellgrenzen | Originale unverändert; kein Gateway-Diff; nur isolierte lokale Review-Kopien bearbeitet. |

Ursprüngliche Sicherheitsbefunde: Der erste Audit nannte Next.js `16.3.1` und
Sharp `0.35.3`. Diese Befunde wurden mit den oben genannten Versionen behoben;
ein pauschales `audit fix --force` wurde nicht ausgeführt.
Die [Next.js-Meldung](https://github.com/advisories/GHSA-2xp9-vwfh-vxw4)
und die [Sharp-Meldung](https://github.com/advisories/GHSA-rgj7-g3m4-5g8c)
beschreiben den AVIF-/libheif-Bezug. Das ist keine Feststellung einer erfolgten
Kompromittierung des Servers; dessen tatsächliche Laufzeit wurde nicht untersucht.

## Noch nicht nachgewiesen

- Reale Staging- oder Produktionsversion, Konfiguration und Speicherreserven.
- Migrationen 0020–0022 gegen das tatsächliche Staging-Schema; der lokale
  Nachweis mit bestehenden Testdaten ersetzt kein tatsächliches Deployment.
- GitHub-CI und Staging-Auslieferung werden nach dem autorisierten Push geprüft.
- Reale Geräteprovisionierung gegen Staging-Organisation/Zone und deren Berechtigungen.
- Tatsächliche TLS-Kette, Proxy-Vertrauen, Netzwerkabbrüche und Wiederanlauf auf Hardware.
- Reale ADS131M04-Aufnahme, Kalibrierung, Timing, vier Kanäle und Hardwaretreiber.
- Persistente mehrtägige Sensorwarteschlange sowie Geräte-Stromausfallfestigkeit.
- 100-Geräte-Dauerlast, CPU-/RAM-Druck und gemeinsamer Host-/Plattenausfall.
- Allgemeines Aufräumen verwaister Objekte nach Objekt-Upload vor DB-Commit.
  Deterministische Schlüssel helfen bei Wiederholung; ein allgemeiner Sweeper fehlt.

Diese offenen Nachweise verhindern eine Aussage wie „auf dem Server garantiert
kompatibel“. Belegt sind die unveränderten Legacy-Quellen relativ zur gewählten
main-Basis und die konkret aufgeführten lokalen Regressionen.

## Reihenfolge nach der gemeinsamen Durchsicht

1. Erledigt: Benutzer wählt main + Direct für develop/Staging.
2. Erledigt: bestehende Änderungen, zusätzliche Sicherheitskorrektur, Schema,
   Migrationen, Altdatenannahme, Fehlerfälle und Rückfallplan lokal prüfen.
3. Gemäss ausdrücklicher Freigabe auf develop pushen und CI/Staging kontrollieren.
4. Bestehenden Backend-Pfad auf Staging zuerst mit deaktiviertem Direct prüfen.
5. Separate Direct-Datenbank, Bucket mit Quota, beschränkte Schlüssel und
   reservierten Speicher einrichten; Schema explizit initialisieren.
6. Ausschliesslich Staging-Proxy ergänzen; Direct zunächst für ein Testgerät aktivieren.
7. Benutzer verbindet erste Sensoren. Alte und neue Uploads gleichzeitig prüfen,
   auch nach Verbindungsabbruch, Wiederholung, Restart und Direct-Abschaltung.
8. Erst nach erfolgreichem Sensorpilot Produktion separat besprechen und freigeben.

GATEWAY ist bei fehlendem Modus der Default. DIRECT benötigt eigene UUID,
Geräteschlüssel, HTTPS-Ziel und CA. DUAL benötigt zusätzlich ein registriertes
Legacy-Testgerät im selben Bereich. Die genaue USB-/NVS-Einrichtung steht in
`GreenMindArdu/examples/direct_sensor/README.md`; API und Serverparameter stehen
in `direct-to-cloud.md` und `docker-compose.direct.yml`.

Rückfall: Direct-API/Worker stoppen und nur dessen zusätzliche Proxy-Location
zurücknehmen. Datenbank, bereits bestätigte Chunks und Bucket bleiben erhalten.
Gateway-Prozesse, alte Endpunkte, Schlüssel und Daten bleiben in Betrieb.
Keine Tabellen-/Bucket-Löschung und keine Schema-Downgrades als Rückfallmassnahme.
