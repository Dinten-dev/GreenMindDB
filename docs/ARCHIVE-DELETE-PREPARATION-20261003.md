# Löschung vorbereiten; Originale behalten

**Aktualisierung 5. Oktober:** Für zukünftige WAV-Löschfreigaben gilt
[die getrennte WAV-Metadatensicherung](ARCHIVE-WAV-METADATA-20261005.md).
Der unten beschriebene sensible Vollkatalog ist eine ältere, inaktive Vorbereitung
und wird weder aktiviert noch als neuer WAV-Wiederherstellungsnachweis verwendet.

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
`GREENMIND_MINIO_CLIENT` muss auf eine absolute, ausführbare MinIO-Client-Datei
zeigen. Vor jeder administrativen Aktion wird ihre Versionskennung geprüft.
Der vorhandene `/usr/bin/mc` auf Production ist **Midnight Commander**, kein
MinIO-Client; er wird deshalb ausdrücklich abgewiesen. Die Prüfung funktioniert
auch unter dem älteren Bash des Macs, ohne sich auf implizites `set -e` für
`[[ ... ]]` zu verlassen.
`python -m app.raw_archive.diagnostics` führt nur die zwei erlaubten Abfragen aus.
`AccessDenied` bedeutet unbekannten Zustand, keinesfalls ausgeschaltete Versionierung.

## Bereits installierte Vorbereitung ergänzen

`deploy/raw-archive/delete-preparation/activate-catalog.py` aktiviert ausschließlich
eine neue private Katalogsicherung für die bereits installierte Vorbereitung
`d344695ef260`. Es prüft deren vollständige unveränderte Datei-Prüfsummen,
Empfangsbaseline, beide Proxy-Prüfsummen, alte Zeitpläne und ausgeschaltete
Löschungsflags. Ziel ist ausschließlich Storage-Box-Konto `u676312`.
Die CLI verlangt `--authorize-private-catalog`, `--destination-user u676312`
und die SHA-256-Prüfsumme der Aktivierungsdatei. Dieser Schalter ersetzt keine
ausdrückliche menschliche Freigabe des sensiblen Inhalts und des Empfängers.

Der neue Auftrag behält 128 MiB RAM, 10 % CPU, keinen Swap und niedrige
I/O-Priorität. Sein eigener Speicherschutz ist auf 640 MiB freie Host-RAM erhöht;
bestehende Kopier- und Empfangsgrenzen werden nicht verändert. Versuche sind
nur bis zur bestehenden, nicht verlängerten 24-Stunden-Frist möglich. Erfolgreiche
Publikation oder Ablauf sperren weitere Sicherungen. Teilergebnisse bleiben
privat erhalten und zählen nicht als vollständige Sicherung.

Die Freigabe wird privat in `catalog-upload-authorization.json` dokumentiert,
die Aktivierung in `catalog-activation.json`. Es werden keine neuen Adminrechte
oder Konten erzeugt. Zum Aussetzen nur den neuen
`greenmind-catalog-backup-d344695ef260.timer` stoppen; Nachweise behalten.
Eine abgeschlossene Sicherung ist erst mit `reports/catalog-published.json`
nach vollständiger Rückleseprüfung bestätigt. Der echte Restore bleibt eine
separate Abnahme. Die Beobachtung, alle alten Dienste und Zeitpläne bleiben erhalten.

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
SQLite benötigt für einen WAL-Leser gegebenenfalls Zugriff auf seine
Shared-Memory-Laufzeitdateien. Die Service-Vorlagen erlauben diesen im privaten
Journalverzeichnis, binden aber die eigentliche Quelldatenbank schreibgeschützt
ein; zusätzlich erzwingen die Verbindungen `mode=ro` und `query_only=ON`.
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

## Fortsetzung: getrennte Abnahmeumgebung

`snapshot_api` verwendet ausschließlich eine explizite, private Operator-Token-Datei
und eine benannte Storage-Box-ID. Der HTTPS-Endpunkt ist auf
`https://api.hetzner.com/v1` festgelegt. Vor einer Erstellung prüft es Benutzer,
Servernamen und Sichtbarkeit des Snapshot-Verzeichnisses. Es gibt keine Methoden
zum Löschen, Zurückrollen oder Ändern einer Snapshot-Retention. Ein unbekanntes
Ergebnis des einmaligen Erstellungsaufrufs wird nicht automatisch wiederholt.
Eine erfolgreiche API-Antwort ist noch keine Abnahme: `--snapshot-id` liest den
Snapshot separat und dessen vollständiges Katalogmanifest über SFTP zurück.
Der vollständige Katalog-Restore und WAV-Rücklesetests bleiben zusätzliche Gates.
Token-Datei und MinIO-Admin-Konfiguration gehören ausschließlich auf den privaten
Host; sie werden nie in einen öffentlichen Reader eingebunden.

`provision-diagnostic.sh` validiert die vollständige Policy: exakt zwei Buckets,
exakt die beiden Metadatenaktionen, keine zusätzlichen Statements oder Wildcards.
Ein regulär vom Betreiber eingerichteter Admin-Alias bleibt Voraussetzung.

`RAW_ARCHIVE_QUARANTINE_FILE` referenziert ein privates Register mit
`schema=1`, `environment=production` und `excluded`-Einträgen aus `kind`/`identity`.
Eine konfigurierte, aber fehlende oder beschädigte Datei blockiert Löschprüfungen.
Quarantäne bleibt auch nach einer späteren Feature-Neuberechnung wirksam.
`investigation --gateway-wav UUID --output NEW_PRIVATE_DIRECTORY` liest genau
ein Original bis 1 MiB, prüft unveränderte Quellidentität, Länge, SHA-256 und
WAV-Decodierung. Es korrigiert weder SQL-Metadaten noch Originalbytes.

Der Katalog enthält jetzt zusätzlich `direct_enrollment`, damit die Verbindung
von Hardware-Adresse und Direct-Gerät beim Restore erhalten bleibt. Der bestehende
isolierte Reader erhält dafür nur `SELECT` auf dieser benannten Tabelle.
Bestehende Schema-/Migrationsarchive bleiben vollständig erhalten.

`reconcile_backup --manifest PRIVATE_MANIFEST --output NEW_PRIVATE_DIRECTORY`
vergleicht den vollständigen geprüften Backupstand gegen sein Journal. Es streamt
JSON/Gzip und verwendet einen eigenen SQLite-Index statt einer großen RAM-Liste.
Es prüft alle Dateiprüfsummen und Zeilenzahlen, zählt fehlende/mismatched Belege
und ungültige Metadaten und unterscheidet die sieben Tage lokale Reserve.
Das ist ein Abgleich des festen Backupstands, kein Beweis für später eingegangene
Daten oder für den aktuellen physischen Zustand jedes entfernten WAVs.
Ohne vollständige neue Backups gibt es keinen vollständigen Abgleich.

`prepare-reader.py` erzeugt eine separate Konfiguration für Port 8141 mit dem
vorhandenen geprüften Dependency-Image und unveränderlichem, schreibgeschütztem
neuem Quellcode. Dashboard 8140 und alle Empfangsrouten bleiben erhalten.
`start-reader.py` startet nur diesen Kandidaten nach Codeprüfsummen und mindestens
704 MiB verfügbarer Hostreserve (512 MiB plus Kandidatenlimit 192 MiB).
Bei fehlgeschlagenem Start wird ausschließlich der Kandidat gestoppt.
`switch-reader.py` verlangt einen frischen, privaten und SHA-gepinnten echten
Abnahmebericht für lokale und reine Archivdownloads, Gateway/Direct, Rechte,
Anmeldung, WAV-Prüfsummen und fehlende Empfangsrouten. `--rehearse` nimmt den
minimalen Wechsel sofort vollständig zurück. Eine fehlgeschlagene Nginx-Prüfung
stellt ebenfalls die ursprünglichen Bytes wieder her. Ohne echten Bericht kein
Proxywechsel; ausgefüllte Vorlagen sind keine Testnachweise.

Mit `install-prepared.py --start-observation --quarantine-gateway UUID` wird nur
der neue, auf 96 MiB und zehn Prozent CPU begrenzte Beobachtungsdienst registriert.
Er sammelt alle fünf Minuten maximal 24 Stunden lang nur lesende Prüfungen.
Nach Ablauf blockiert `finished.json` weitere Ausführungen. Er prüft Receiver-
Startzeiten/Neustarts, unveränderte Proxies, Health und bei ausreichender Reserve
jeweils die letzte tatsächlich gespeicherte Gateway-/Direct-Aufnahme.
Lücken über zehn Minuten, fehlender Fortschritt, Receiveränderungen oder Fehler
verhindern PASS. Ressourcenpausen werden gezählt. Der Bericht erteilt niemals
Löschfreigabe und ersetzt weder Snapshot-/Restore- noch Bestandsabnahme.
Rücknahme: nur den neu benannten Beobachtungstimer stoppen/deaktivieren;
Empfangsdienste, Kopierzeitpläne, Reader 8140 und Originaldateien bleiben bestehen.

Alle Optionen sind explizit. Keine Löschung, kein Pruning und keine alte
Retention werden durch Einrichtung, Diagnose, Kandidatenstart oder Beobachtung aktiviert.

`--start-catalog-backup` registriert zusätzlich einen eigenen Metadatenauftrag,
der maximal 24 Stunden alle 30 Minuten mit den unveränderten Ressourcenwächtern
versucht, das vollständige Katalogbackup zu veröffentlichen. Nach Erfolg oder
Ablauf wird er durch eine private Abschlussdatei blockiert. Ablauf oder Pause
werden ausdrücklich nicht als erfolgreiches Backup gezählt. `catalog_once`
startet keinen RAW-Kopierer und führt keinen Restore in Live-Datenbanken aus.
Vollständige echte Wiederherstellung bleibt nach Veröffentlichung erforderlich.

Snapshot-Verwaltung: [Hetzner-Dokumentation](https://docs.hetzner.com/storage/storage-box/snapshots/).
API-Pfade und Snapshot-Schema: [offizieller Hetzner-Client](https://github.com/hetznercloud/hcloud-go/blob/main/hcloud/storage_box_snapshot.go).
Bestehende `null`-Objekte: [S3-Versionierungsablauf](https://docs.aws.amazon.com/AmazonS3/latest/userguide/versioning-workflows.html).
