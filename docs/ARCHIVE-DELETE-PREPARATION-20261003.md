# Löschung vorbereiten; Originale behalten

Die neue Schutzlogik ist vorbereitet. **Keine WAV-Löschung ist freigegeben.**
Bestehende Storage-Box-WAVs werden weder ersetzt noch erneut gesammelt hochgeladen.
Gateway, Direct und Legacy behalten ihre Empfangsdienste und Endpunkte.

## Implementierte Schranken

- Mindestens sieben volle Tage lokale Reserve nach dem spätesten Zeitpunkt aus
  Aufnahmeende, Eingang und erster erfolgreicher Archivprüfung. Wiederholte
  Prüfungen verschieben diese erste Prüfzeit nicht. Alte Belege ohne diesen Wert
  verwenden konservativ ihre letzte nachweisbare Prüfzeit.
- Ein privates, mit SHA-256 festgelegtes Manifest erlaubt ausschließlich
  benannte Quellstände. Es verfällt nach spätestens 24 Stunden und begrenzt den
  ersten Pilot auf zehn Dateien und insgesamt fünf MiB.
- Original und Storage-Box-Snapshot werden vollständig zurückgelesen;
  Dateilänge und SHA-256 müssen übereinstimmen. Der Snapshot muss nach der
  ursprünglichen Archivprüfung entstanden und höchstens 24 Stunden alt sein.
- Wiederherstellungsindex, Katalogmanifest und erfolgreicher Restore-Nachweis
  müssen ebenfalls im Snapshot liegen. Ein Dateilisting reicht nicht.
- Aktuelle verifizierte Features und bei Direct die Visualisierungsprojektion
  bleiben verpflichtend. Fehlende, widersprüchliche oder beschädigte Belege
  blockieren die betreffende Datei.
- S3-Löschung verwendet ausschließlich eine explizite Version. Alte `null`
  Versionen bleiben standardmäßig gesperrt; freigegebene Unterstützung erfordert
  aktivierte Bucket-Versionierung, deaktivierte Lifecycle-Regeln und einen
  organisatorischen Änderungsstopp der Versionierung während des Pilots.

## Diagnosezugang

`deploy/raw-archive/delete-preparation/diagnostic-policy.json` erlaubt nur
`GetBucketVersioning` und `GetLifecycleConfiguration` auf den zwei benannten
WAV-Buckets. Den Platzhalter für den exakten Direct-Bucket ersetzen.
Keine Objekt-, Schreib-, Lösch-, Benutzer- oder Administrationsrechte vergeben.

`provision-diagnostic.sh` verwendet einen **ausdrücklich vom Betreiber
eingerichteten** MinIO-Admin-Alias und neu bereitgestellte Zugangsdaten.
Es entdeckt keine Root-Zugangsdaten. Der Diagnosezugang bleibt ausschließlich
beim privaten Host-Worker und wird nie in öffentliche Reader eingebunden.
`python -m app.raw_archive.diagnostics` führt nur die zwei erlaubten Abfragen aus.
`AccessDenied` bedeutet unbekannten Zustand, keinesfalls ausgeschaltete Versionierung.

Das bisherige Auslesen von MinIO-Root-Zugangsdaten aus einem Container wurde
durch die automatische Freigabeprüfung abgelehnt. Diesen Weg nicht erneut
verwenden. Die neue Rolle ist deshalb vorbereitet, ihre Einrichtung benötigt
einen regulär bereitgestellten administrativen Zugang.

## Wiederherstellung

`app.raw_archive.recovery` erstellt einen konsistenten SQLite-Online-Backupstand
einschließlich WAL. PostgreSQL-Kataloge werden getrennt in nur lesenden,
wiederholbar konsistenten Transaktionen gesichert: Organisationen, Benutzer,
Zonenrechte, Sensoren, WAV-Metadaten, Features und verdichtete Visualisierung.
Rohmessungen und laufende Direct-Payloads werden nicht kopiert.
Die zwei PostgreSQL-Snapshots und das Journal sind jeweils konsistent, aber
**kein atomarer gemeinsamer Snapshot aller drei Datenbanken**; per-Datei-Belege
und erneute Quellprüfungen bleiben notwendig.

Backups sind privat und werden ausschließlich unter Inhaltsprüfsummen nach
`production/catalog-backup/` angehängt. Jede Veröffentlichung liest ihre gesamten
Bytes erneut zurück. Große Kataloge, Zeitüberschreitungen oder Ressourcenmangel
blockieren statt Schutzgrenzen zu lockern. Die Transportgrenze beträgt 64 MiB
pro komprimierter Datei; darüber ist vor Aktivierung eine geprüfte Aufteilung
notwendig. Backups enthalten sensible Benutzermetadaten und Passwort-Hashes:
keine öffentlichen Freigaben oder Download-Links erstellen.

Restore benötigt zwei **bereits migrierte, leere Testdatenbanken** mit Namen
`greenmind_restore_*`. Live-Datenbanken und vorhandene Zieljournale werden
abgewiesen. Bereitstellen über:

- `RAW_ARCHIVE_RESTORE_GATEWAY_DATABASE_URL`
- `RAW_ARCHIVE_RESTORE_DIRECT_DATABASE_URL`

`recovery --restore-reference PRIVATE_REFERENCE.json --output NEW_PRIVATE_DIRECTORY`
liest alle veröffentlichten Backupdateien zurück, prüft Transport- und
Originalprüfsummen und vergleicht die wiederhergestellten Zeilenzahlen.
`--snapshot SNAPSHOT_NAME` prüft zusätzlich den tatsächlichen Snapshot-Leseweg.
`--publish-catalog` veröffentlicht ausschließlich den fertigen Restore-Nachweis.
Es werden dabei niemals ursprüngliche WAVs entfernt oder überschrieben.

## Download-Kompatibilität

Der isolierte Reader kann mit `ARCHIVE_COMPAT_READS_ENABLED=true` die bisherigen
Gateway-Datei-/Featurelisten, Einzel-WAVs und ZIP-Adressen bedienen. Zonenrechte,
Anmeldung und Dateinamen bleiben unverändert. Direct-Geräte können ausschließlich
eigene Segmente und Runs lesen; TLS und Geräte-Token bleiben vorgeschrieben.
Der Reader registriert keine WAV-, Chunk- oder sonstigen Empfangsrouten.

`app.raw_archive.compat_proxy.draft` erstellt ausschließlich einen zusätzlichen
GET-Proxyentwurf zum Reader 8140. Direct-Run-GETs werden innerhalb des vorhandenen
`^~`-Blocks ergänzt, damit sie tatsächlich erreichbar sind. Aufnahme, Gateway-
Kontrollschutz, Administration, Frontend und bisherige statische Rückfälle bleiben
bytegenau erhalten. SHA-256 schützt gegen zwischenzeitliche Konfigurationsänderungen;
`rollback` stellt die ursprünglichen Bytes wieder her. Kein generischer
Release-Proxygenerator und keine automatische Live-Umschaltung.

## Serverpaket und Rücknahme

`install-prepared.py` installiert ein separates, unveränderliches Paket unter
`/opt/greenmind/delete-preparation/<revision>` und private Berichte/Backups auf
dem Datenvolume. Diagnosezugang, Reader-Kompatibilität und Löschung bleiben aus.
Die Service-Vorlagen werden nicht registriert oder gestartet. Backup und
Nur-Lese-Probelauf sind auf 128 MiB, zehn Prozent CPU, keinen Swap, niedrige
Priorität, 512 MiB Hostreserve und Last 2,4 begrenzt.

Optional `--activate-copy-code` wechselt nur den Codepfad des vorhandenen
Kopierdiensts für seinen nächsten regulären Lauf. Timer, Empfangscontainer,
Speicher-/CPU-Limits und privater Kopierzugang bleiben unverändert. Der bestehende
Launcher erzwingt weiterhin Kopierbetrieb ohne Lösch-/Retention-Rechte.
Die neue erste Prüfzeit wird damit dauerhaft gespeichert.

Rücknahme dieses Codepfads: die neue
`90-first-verification.conf` in einen nicht aktiven Sicherungspfad verschieben,
Service-Konfiguration neu einlesen und den nächsten normalen Timerlauf abwarten.
Der bisherige Starter `/opt/greenmind/raw-copy/20260928-opt/optimized.py` bleibt
vollständig erhalten. Keine Empfangsdienste neu starten. Keine WAVs löschen.

## Vor späterer Löschfreigabe

1. Diagnoseidentität regulär einrichten; Versionierung und Lifecycle tatsächlich prüfen.
2. Storage-Box-Snapshots durch den Kontobetreiber einrichten und zurücklesen.
   SSH-Zugang zur Storage Box alleine erlaubt keine Snapshot-Verwaltung.
3. Aktuelle Katalogbackups veröffentlichen; beide Testdatenbanken vollständig
   wiederherstellen. Aktuelle Schemaexporte/Migrationsstände ebenfalls sichern.
4. Das fehlerhafte alte Gateway-Metadatum untersuchen; bis zur belegten Reparatur
   bleibt diese Datei ausdrücklich von jeder Löschung ausgeschlossen.
5. Reader-Kompatibilität im Kandidaten starten, echte alte Downloads mit
   fehlender lokaler Quelle prüfen; danach den minimalen Proxywechsel abnehmen.
6. Über 24 Stunden nur lesend prüfen, einschließlich Empfangsfortschritt,
   Ressourcenpausen, Snapshot- und Restore-Belegen. `preflight --verify-proof`
   führt alle Dateischranken ohne S3-Löschung aus. Keine abgelaufenen oder
   unvollständigen Nachweise als bestandene Abnahme zählen.
7. Aktuelle Bestandsabstimmung und alle offenen Ausnahmen dokumentieren;
   anschließend separat eine ausdrückliche menschliche Pilot-Löschfreigabe einholen.

Es gibt weiterhin nur die gewählte Storage Box als RAW-Archiv. Ein Snapshot
schützt gegen manche Dateifehler, nicht gegen Verlust desselben Kontos/Anbieters.
Die Quellversion bleibt erhalten, solange irgendein erforderlicher Nachweis fehlt.

Snapshot-Verwaltung: [Hetzner-Dokumentation](https://docs.hetzner.com/storage/storage-box/snapshots/).
Bestehende `null`-Objekte: [S3-Versionierungsablauf](https://docs.aws.amazon.com/AmazonS3/latest/userguide/versioning-workflows.html).
