# Tägliche WAV-Auslagerung – vorbereitet, nicht aktiviert

**Nachtrag 25.09.2026:** Die reine Kopierfunktion ist separat auf Production
installiert und getestet, mit geschütztem Nacht-Timer. Löschung und produktive
Archiv-Lesewege bleiben aus. Aktueller Stand und Kapazitätsgrenzen:
[Production-Kopierbetrieb](RAW-WAV-STORAGEBOX-PRODUCTION-COPY.md).
Der folgende Vorbereitungsstand beschreibt die vollständige Auslagerung mit Löschung.

Stand: 25.09.2026, nach Präzisierung der Anforderung. **Keine Dreimonatsfrist mehr:**
Abgeschlossene WAVs sollen jede Nacht auf die Storage Box übertragen, vollständig
geprüft und anschließend auf dem Hauptserver entfernt werden. PostgreSQL-Daten,
Features und Metadaten bleiben erhalten. Diese Logik ist lokal implementiert;
Deployment und Aktivierung sind weiterhin nicht freigegeben und nicht erfolgt.

## Zeitplan und Ordner

Vorbereiteter Start: täglich **00:00 Europe/Zurich**, einschließlich Sommer-/Winterzeit.
Die systemd-Vorlage hat maximal eine Minute Toleranz. Nach einem Neustart gibt es
keinen sofortigen Nachhollauf; offene Arbeiten werden beim nächsten Nachtlauf fortgesetzt.

Die Storage Box erhält einen Ordner pro **Aufnahmetag und Sensor**, beispielsweise:

```text
greenmind-raw/
  production/
    2026-09-25/
      mac-14-c1-9f-d9-42-a4/
        091500000000_gateway_<object-id>_<sha256>.wav
      mac-14-c1-9f-d9-ab-e8/
        110000000000_direct_<object-id>_<sha256>.wav
```

Der Tag richtet sich nach dem **Aufnahmebeginn in Schweizer Zeit**, nicht nach dem
Upload. Über Mitternacht reichende Dateien bleiben ungeteilt im Starttag-Ordner.
Sensoren werden durch normalisierte MAC-Adressen identifiziert; ist keine MAC
katalogisiert, dient die stabile Gateway-Sensor-/Direct-Geräte-UUID als Ersatz.
Namensänderungen im Dashboard verschieben keine Archivdateien. UTC-Offsetwechsel,
gleichzeitige Aufnahmen und verschiedene Quellen verursachen dank Datei-ID und
SHA-256 keine Namenskollisionen. Staging hat einen eigenen obersten Ordner.

## Prüfung vor jeder lokalen Löschung

1. Gateway-WAVs und versiegelte Direct-Revisionen aus ihren Katalogen lesen.
   Neue Aufnahmen werden vor historischen Rückständen priorisiert.
2. Einen festen S3-Quellstand herunterladen. Länge und SHA-256 müssen zum Katalog passen.
3. Unter eindeutigem `.partial`-Namen hochladen, anschließend final umbenennen.
   Vorhandene Dateien müssen identisch sein; Verbindungsfehler gelten nicht als „fehlt“.
4. Die finale Archivdatei über eine neue Verbindung **vollständig zurücklesen**.
   Länge und SHA-256 müssen exakt stimmen. Existenz oder Dateigröße allein reichen nicht.
5. Den Kopierbeleg mit Quelle, Version, Archivpfad, Prüfsumme und Prüfzeit dauerhaft
   im separaten Journal speichern. SQLite verwendet WAL und FULL-Synchronisierung.
6. Vor Löschung erneut aktuelle Features, Quellidentität und bei Direct die
   Dashboard-Projektion prüfen. Abgenommene Archiv-Lesewege sind Pflicht.
7. Das Archiv **noch einmal vollständig zurücklesen und vergleichen**. Erst danach
   eine dauerhafte Löschabsicht schreiben und exakt die geprüfte S3-Version entfernen.
8. Prüfen, dass diese Version nicht mehr existiert, und den Abschluss protokollieren.

Scheitert eine Prüfung, bleibt das Original lokal. Eine erfolgreiche Kopie allein
erzwingt keine Löschung: fehlende Features, fehlende Freigaben oder unklarer
Speicherzustand halten sie zurück. Nach einem Absturz zwischen Löschung und
Abschlussbeleg ist eine geprüfte Wiederaufnahme möglich.

Nur abgeschlossene Kalendertage werden lokal entfernt. Neue/unfertige Dateien
bleiben lokal; zusätzlich gilt eine Ruhezeit von zehn Minuten vor der Auswahl.
Spät fertiggestellte oder spät eingegangene Dateien kommen im nächsten Nachtlauf.
Es gibt keine pauschale Ordnerlöschung und keine Änderung am manuellen 29-GB-Export.

**Wichtig: unversionierte und `null`-S3-Versionen werden nicht gelöscht.** Ein
ungeprüftes Löschen nur nach Dateiname hätte ein Rennen mit erneuten Uploads.
Weitere Versionen, Löschmarker oder aktive Bucket-Lifecycle-Regeln blockieren
ebenfalls. Die tatsächliche MinIO-Konfiguration und gegebenenfalls eine sichere
Migration alter Objekte müssen vor Aktivierung abgenommen werden; bloßes späteres
Einschalten der Versionierung macht alte `null`-Objekte nicht automatisch sicher.

## Dashboard, Downloads und Neuberechnung

Der vorbereitete Leseweg `app.raw_archive.reader` ist standardmäßig deaktiviert.
Bei späterer Freigabe und einem echten S3-„nicht gefunden“ können Gateway-Downloads,
ZIP-Bundles, WAV-/Feature-Neuberechnung, Gateway-Wellenformen und Direct-Artefakte
die geprüften Originalbytes aus der Storage Box lesen. Bestehende Firmen- und
Zonenberechtigungen bleiben vor dem Speicherzugriff bestehen. Keine öffentlichen
Storage-Box-Links, neuen API-Endpunkte oder Frontendänderungen.

Authentifizierungsfehler oder allgemeine S3-Ausfälle werden nicht als fehlende
Datei interpretiert. Archivbytes werden erst nach vollständiger Prüfung freigegeben.
Maximal zwei Archivstreams pro Prozess begrenzen parallele Rücklesevorgänge;
temporäre Dateien und Slots werden beim Schließen freigegeben. Abgebrochene
ZIP-Downloads beenden ihren Queue-Schreiber. Fehlende/kaputte WAVs brechen den
Download ab, statt stillschweigend ein scheinbar vollständiges ZIP zu liefern.

`raw_deleted_at` wird nicht gesetzt: die Originaldaten sind weiterhin im Archiv
verfügbar. Der Archivstatus liegt im separaten Journal. Manuelle Presigned-S3-URLs
werden nicht auf die Storage Box umgebogen; die vorhandenen Downloadrouten proxien
die Daten durch das Backend. Live-Abnahme dieser Routen bleibt vor Löschfreigabe nötig.

## Grenzen zum Schutz des Betriebs

| Grenze pro Nacht | Standard |
|---|---:|
| WAV-Versuche | 5.000 |
| Originalvolumen einschließlich Fehlversuchen | 1 GiB |
| Laufzeit zwischen Arbeitsschritten | 1 Stunde |
| Katalogreferenzen / Seitengröße | 10.000 / 100 |
| Einzeldatei | 8 MiB, maximal 64 MiB konfigurierbar |
| Quelle / SFTP | jeweils etwa höchstens 1 MiB/s |
| Verfügbarer Host-RAM | mindestens 1 GiB |
| Freier Arbeitsbereich | mindestens 2 GiB plus dreifache Dateigröße |
| CPU / Prozessspeicher in der Service-Vorlage | 25 % / maximal 256 MiB |

Die Netzlast ist höher als das Originalvolumen, da Upload und mehrfaches Rücklesen
dazukommen. Die Grenzen sind **keine Zusage**, dass ein beliebig großer Bestand in
einer Nacht fertig wird. Größere Rückstände bleiben sichtbar und werden fortgesetzt.
Zu große oder unvollständig katalogisierte Dateien werden als Fehler gemeldet,
nicht stillschweigend ausgeschlossen. Tageszuwachs und Rückstand vor Aktivierung messen.

Ein Prozess-Lock verhindert parallele Läufe. RAM-/Last-/Dienstprüfungen und `PAUSE`
im Zustandsverzeichnis stoppen an Prüfpunkten; Netzwerkaufrufe enden spätestens mit
ihrem Timeout bzw. der Service-Laufzeitgrenze. SFTP-Timeout beendet auch SSH-Kinder.
Drei aufeinanderfolgende Fehler stoppen den Lauf. Ein fehlgeschlagener Eintrag
kann nicht permanent alle anderen Referenzen verdrängen.

Reine Sicherung pausiert bei weniger als 5 %/2 GiB freiem Quellspeicher. Eine
**separat freigegebene Auslagerung** darf ein volles Quellvolume dagegen entlasten;
die unabhängigen Scratch-/Journal-, RAM- und Dienstprüfungen bleiben Pflicht.

PostgreSQL bleibt schreibgeschützt: 3 Sekunden Statement-, 500 ms Lock-Timeout.
Die separat vorbereiteten Leseindizes unter `deploy/raw-archive/*-read-index.sql`
werden niemals automatisch installiert. Der Runner prüft gültige Indizes und
Read-only-Verbindungen vor seinen Scans. Eine spätere Installation braucht eine
Freigabe, `CREATE INDEX CONCURRENTLY` und Beobachtung von Platz, I/O und Abfrageplänen.

## Deaktivierte Konfiguration

Vorlage: `backend/app/raw_archive/storagebox.env.example`. Alle Freigaben sind aus:

- `RAW_ARCHIVE_ENABLED=false`: kein Worker-Transfer.
- `RAW_ARCHIVE_DELETE_ENABLED=false`: keine lokale Entfernung.
- `RAW_ARCHIVE_READS_ENABLED=false`: kein API-/Worker-Archiv-Fallback.
- `RAW_ARCHIVE_READS_ACCEPTED=false`: Archiv-Lesewege noch nicht live abgenommen.

Ohne Aktivierung liefert `python -m app.raw_archive` nur `disabled`, null Transfers
und null Löschungen. Er öffnet dabei weder Datenbanken noch Netzwerkverbindungen.
Die systemd-Vorlagen verlangen zusätzlich `/etc/greenmind/raw-archive/APPROVED`;
der Timer müsste ausdrücklich installiert und aktiviert werden. Nichts davon
wurde auf dem Server eingerichtet oder freigeschaltet.

Ziel: `u676312@u676312.your-storagebox.de`, Port 23, eigener Ordner `greenmind-raw`.
Geprüfte Hostschlüssel und eigene SSH-Schlüssel als geschützte Dateien einbinden,
keine Schlüsselannahme auf Verdacht. Alle `FILL_...`-Konfigurationswerte müssen
vorher geprüft werden. PostgreSQL-Konten nur mit SELECT-Rechten. Für erste
Kopiertests S3-Read-only-Konten; erst zur gesondert abgenommenen Entfernung
bucketbegrenztes `DeleteObjectVersion` plus benötigte Versions-/Lifecycle-Leserechte.
Kein unversioniertes `DeleteObject`, keine Put-/Bucket-Konfigurationsrechte nötig.

Für Archivleser in allen beteiligten API-/Projektions-/Feature-Prozessen das Journal
lesbar und konsistent zugänglich machen. Sie benötigen eigene sichere Scratch-
Verzeichnisse und den geprüften Storage-Box-Zugang. Das Journal selbst über die
SQLite-Backup-API sichern; nicht nur die Hauptdatei bei aktiven WAL-Schreibvorgängen.
Die beiden bisherigen destruktiven Retention-Worker müssen deaktiviert bleiben.

## Status, Rücknahme und verbleibende Abnahme

JSON-Berichte enthalten Kopien, Löschungen, Prüfungen, Bytes, Fehler, offene
Referenzen und Scanfortschritt. `incomplete`/`blocked` endet mit Exitcode 1;
ein begrenzter oder fehlgeschlagener Lauf wird nicht als vollständiger Erfolg gemeldet.
Den systemd-Status und das Alter des letzten Berichts zusätzlich überwachen:
ein hart beendeter Prozess kann keinen Abschlussbericht schreiben.

Spätere Rücknahme: Timer/Worker stoppen und Kopier-/Löschflags deaktivieren.
**Archiv-Lesewege und Journal erhalten**, solange Originale extern liegen.
Keine Rückmigration nötig, um neue Transfers zu stoppen. Bereits ausgelagerte
Originale können nach verifizierter Wiederherstellung wieder lokal bereitgestellt werden.

Vor Aktivierung fehlen reale Storage-Box-/MinIO-Tests, Schlüssel-/Quota-/Versions-
prüfung, Lastmessung, Dimensionierung, Alarmierung und produktive Leseweg-Abnahme.
`sftp -f` fordert Flush an; dessen tatsächliche Unterstützung muss am Ziel geprüft
werden. Rücklesen beweist aktuell verfügbare Bytes, keine Garantie gegen den
späteren Ausfall der einzigen Archivkopie. Snapshots und Journal-Wiederherstellung
sind deshalb Teil der Abnahme vor lokaler Löschung.

Ergebnisse und Testbefehle: [Prüfbericht](RAW-WAV-STORAGEBOX-TESTREPORT.md).
