# Prüfbericht: tägliche WAV-Auslagerung nach Tag und Sensor

**Nachtrag 25.09.2026:** Die anschließende reine Kopier-Abnahme auf Production ist
im [Production-Prüfbericht](RAW-WAV-STORAGEBOX-PRODUCTION-COPY.md) dokumentiert.
Dieser Bericht beschreibt die vorherige lokale Abnahme, nicht den aktuellen
Deployment-Status. Löschung bleibt weiterhin deaktiviert.

Stand: 25.09.2026, nach Präzisierung „jede Nacht sichern, prüfen, dann lokal löschen“.
Basis: `main`, `92e29a3e18328bf67fab7954f32cdb34a4a2ef5c`.
**Nur lokal vorbereitet: kein Commit/Push/Deployment, keine aktivierte Policy und
keine produktive Übertragung oder Löschung.**

## Ergebnisse des abschließenden Laufs

| Prüfung | Ergebnis |
|---|---|
| Archiv-, tägliche Auslagerungs- und Lesewegtests | **66 bestanden** |
| Bestehende Gateway-/Direct-/WAV-/Visualisierungs-/Berechtigungstests | **68 bestanden** |
| Insgesamt | **134 bestanden, 1 übersprungen, 0 fehlgeschlagen** |
| Ruff für geänderte Backend-Dateien und Archivtests | bestanden |
| Python-Kompilierung | bestanden |
| Deaktivierter CLI-Einstieg | `disabled`, 0 Transfers, 0 Löschungen |
| PostgreSQL-Kataloge, Leseindizes, Schreibschutz | echte separate lokale Testdatenbank |
| SFTP-Upload, Rücklesen, Wiederherstellen, Korruption, Prozessabbruch | echter lokaler OpenSSH-SFTP-Prozess |
| S3-Versionen, ETag, Löschparameter | Botocore-SDK-Vertragstests |
| Reale Storage Box, realer MinIO, Produktionslast | **nicht ausgeführt** |

Der übersprungene Test ist der bestehende isolierte MinIO-/FLAC-Integrationstest.
Die zuvor versuchten offiziellen MinIO-Image-Abrufe scheiterten an Registry-
Zugriffen. Es wurde keine produktive Instanz als Ersatz-Testziel verwendet.
Zwei bestehende Warnungen betreffen Starlette/httpx und Python-`crypt`/Passlib.

## Änderungen gegenüber der vorherigen Vorbereitung

- Die Dreimonatsfrist ist entfernt. Der Nachtlauf kann WAVs abgeschlossener
  Schweizer Kalendertage nach gesonderter Freigabe auslagern.
- Archivpfade folgen Umgebung / Aufnahmetag / stabile Sensor-MAC oder Geräte-ID.
  Datei-ID und SHA-256 verhindern Namenskollisionen. MAC-Schreibweisen werden normalisiert.
- Kopier- und Löschphase bleiben getrennt abgesichert. Vor Löschung erfolgt ein
  zweites vollständiges Rücklesen, zusätzlich zur Prüfung nach dem Upload.
- Fehlende Features oder eine fehlgeschlagene Prüfung verhindern die Entfernung.
- Tageslauf-Berichte enthalten tatsächliche Löschungen und verbleibende Fehler.
- Hauptserver-WAVs können nach Freigabe aus dem Archiv gelesen werden: Downloads,
  ZIPs, Feature-/Wellenform-Neuberechnung und Direct-Artefakte nutzen einen
  standardmäßig deaktivierten, vollständig prüfenden S3-Miss-Fallback.
- Die ursprünglichen Firmen-/Zonenprüfungen bleiben bestehen. Expliziter Test:
  fehlende Zonenberechtigung führt zu 404, bevor Archivzugriff möglich wird.
- ZIPs verschweigen fehlende WAVs nicht mehr. Fehler brechen den Stream ab;
  Client-Abbrüche hinterlassen keinen blockierten Queue-Schreiber.
- Ein voller Quell-Datenträger allein blockiert eine ausdrücklich freigegebene
  Entlastung nicht. Freier Arbeitsbereich, RAM und Dienstgesundheit bleiben Pflicht.

Bestehende Quelldateien mit kleinen Lesewegänderungen:

- `backend/app/services/wav_service.py`
- `backend/app/direct/storage.py`
- `backend/app/visualization/api.py`
- `backend/app/visualization/worker.py`

Keine Frontend-, Gateway-Firmware-, Empfangsprotokoll- oder produktiven Schema-
Änderungen. Die zusätzlichen Katalogindizes liegen weiterhin nur als getrennte,
nicht ausgeführte SQL-Vorlagen vor.

## Sicherheitsfälle

Geprüft werden unter anderem: byteidentische reale 380-Hz-WAVs, Schweizer Datum
und Winterzeitwechsel, Daten unter drei Monaten, mehrere Sensoren/Quellen,
Direct-Revisionen mit mehreren WAV-Runs, Priorisierung aktueller Dateien,
Warteschlangenfortsetzung, Prüfsummen, beschädigte/fehlende Archivkopien,
Verbindungsfehler, unvollständige Downloads, Versionswechsel, deaktivierte
Defaults, fehlende Feature-Verifikation, fehlende Leseweg-Abnahme, Ressourcen-
mangel, parallele Starts, aktive alte Retention und S3-Lifecycle-Regeln.

Der Löschtest prüft die Reihenfolge: Kopieren → vollständiges Rücklesen →
zweites vollständiges Rücklesen → dauerhafter Löschbeleg → Original entfernen.
Wird die zweite Rückleseprüfung beschädigt, bleibt das Original erhalten.
Die physische Löschung wird ausschließlich mit Testdoubles/SDK-Stubs ausgeführt.

Der Mitternachtsausdruck wurde in der vorherigen Prüfung am selben Tag rein
lesend auf dem Zielserver mit systemd geprüft, einschließlich Winterzeitwechsel.
Archivdienst und Timer waren dort `inactive`; während dieser Anpassung wurden
keine Serverdateien installiert oder Dienste gestartet.

## Reproduzieren

Im `backend`-Verzeichnis mit Projektabhängigkeiten, Pytest und Ruff:

```bash
SKIP_DOCKER_TESTS=1 python -m pytest tests/raw_archive -q
ruff check app/raw_archive tests/raw_archive app/services/wav_service.py app/direct/storage.py app/visualization/api.py app/visualization/worker.py
python -m compileall -q app/raw_archive
RAW_ARCHIVE_ENABLED=false python -m app.raw_archive
```

Für alle Tests zusätzlich eine wegwerfbare lokale PostgreSQL-Datenbank
`archive_test` über `RAW_ARCHIVE_TEST_POSTGRES_URL` konfigurieren. Die Tests
legen darin eigene zufällige Schemas an und entfernen diese anschließend.
`VISUAL_TEST_DATABASE_URL` muss für die vorhandenen Live-Visualisierungstests auf
die separate lokale Datenbank `visual_test` zeigen. Niemals Production verwenden.

Echte lokale SFTP-Tests benötigen `/usr/bin/sftp` und entweder
`/usr/libexec/sftp-server` oder `/usr/lib/openssh/sftp-server`. Fehlen diese
Komponenten oder die Datenbank-Variablen, werden entsprechende Tests übersprungen.
Diese übersprungenen Prüfungen dürfen nicht als bestanden gezählt werden.

JUnit-Ergebnis, Quellen-Prüfsummen und Git-Diff liegen im übergeordneten Projekt
unter `implementation/validation/raw-offload-20260925/`.

## Aktivierungsblocker

1. Storage-Box-Verbindung, Schlüssel, Quota, tatsächliche Flush-Unterstützung und
   Wiederherstellung mit einem eigenen Testordner real prüfen.
2. Reale MinIO-Integration und Versionierung prüfen. **Unversionierte/`null`-
   Objekte bleiben lokal**, bis eine sichere Strategie geprüft ist; keine
   ungesicherte Löschung allein nach Dateiname implementiert.
3. Feature-Verfügbarkeit, neue Leseindizes und tägliche Datenmenge kontrollieren.
   Sicherheitslimits können Rückstände über mehrere Nächte verteilen.
4. Archivleser in allen betroffenen Backend-/Projektions-/Feature-Prozessen
   getrennt bereitstellen und öffentlich mit Berechtigungen prüfen. Danach erst
   `RAW_ARCHIVE_READS_ACCEPTED` freigeben.
5. Journal konsistent sichern, Notfallwiederherstellung, zusätzliche Archiv-
   Absicherung, Lastmessung und Betriebsalarmierung abnehmen.
6. Erst nach ausdrücklicher Aktivierungsanweisung Timer, Transfer und Löschung freigeben.

Die neue Logik ist lokal geprüft. Eine vollständige Live-Abnahme ist damit noch
nicht behauptet; alle Aktivierungsflags bleiben auf `false`.
