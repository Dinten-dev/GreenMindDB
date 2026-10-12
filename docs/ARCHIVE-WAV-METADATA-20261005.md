# WAV-Löschung: getrennte Metadaten und Freigaben

Diese Vorbereitung ersetzt den Vollkatalog als Grundlage zukünftiger
WAV-Löschfreigaben. Keine Löschung ist freigegeben. `activate-catalog.py`
und der alte Vollkatalog-Timer bleiben inaktiv. Benutzerkonten werden nicht
auf die Storage Box exportiert.

## Geänderter Code und Ziel

Die neue Vorbereitung wird separat auf Hetzner bereitgestellt. Die derzeitigen
Empfangsdienste, Kopierprogramme, Reader und Proxys werden nicht ersetzt.

`app.raw_archive.wav_catalog` exportiert ausdrücklich benannte Spalten:
Datei-/Sensor-/Zonen-/Firmen-IDs, wissenschaftliche Parameter, Features einschließlich
`wav_feature_version`, Visualisierungsaggregate und bereinigte Archivbelege.
Benutzer, Zonenberechtigungen, E-Mails, Passwort-Hashes, Gateway-/Geräteschlüssel,
Namen, Beschreibungen und Worker-Fehlertexte sind ausgeschlossen.
Unbekannte JSON-Felder blockieren den Export. Direct-Manifeste werden unverändert
und mit ihrer ursprünglichen Prüfsumme validiert; es wird nichts still redigiert.

Format `wav_metadata_v1`, Schema 2, enthält komprimierte Teile mit höchstens
16 MiB dekodierten Daten. Ein Datensatz ist auf 1 MiB begrenzt. SQL-Zugriffe
sind `REPEATABLE READ`, ausschließlich lesend und mit begrenzten Statements.
Jede Quelldatenbank besitzt ihren eigenen Snapshot; ein gemeinsamer atomarer
Snapshot über Gateway, Direct und Journal wird nicht behauptet.

Nur ein vollständig abgeschlossener Export erhält ein Manifest. Unterbrochene
Exporte bleiben als private, unvollständige Nachweise erhalten und gelten nicht
als Sicherung. Die Veröffentlichung abgeschlossener Exporte ist wiederholbar,
ohne SQL erneut zu lesen. Ein unterbrochener SQL-Snapshot wird nicht mit späteren
Zeilen zu einer vermeintlich konsistenten Sicherung zusammengesetzt.

## Aufruf unter dem richtigen Dienstkonto

Der bestehende Schlüssel ist `root:greenmind-raw-copy`, Modus `0640`.
OpenSSH akzeptiert ihn unter `greenmind-raw-copy`; ein Root-SFTP-Prüfaufruf
verweigert seine Nutzung mit `bad permissions`. Die Rechte bleiben unverändert.
Private Konfiguration durch systemd als EnvironmentFile bereitstellen,
den Prozess als `greenmind-raw-copy` starten. Keine Schlüssel kopieren.

Neue Sicherung: `python -m app.raw_archive.wav_catalog --ledger
/var/lib/greenmind-raw-copy/archive.sqlite3 --output NEW_PRIVATE_DIRECTORY`.
`--publish` veröffentlicht ausschließlich diese WAV-Metadaten beim bestehenden
Storage-Box-Konto `u676312`, unter `greenmind-raw/production/catalog-backup/`.
Jeder veröffentlichte Inhalt wird vom vorhandenen Transport vollständig
zurückgelesen. Alle Dateinamen sind inhaltsadressiert; RAW-WAVs bleiben unverändert.

Wiederaufnahme der Veröffentlichung: `--publish-existing PRIVATE_MANIFEST.json`.
Wiederherstellung: `--restore-reference PRIVATE_REFERENCE.json --output
NEW_PRIVATE_DIRECTORY`; optional `--snapshot PROVIDER_SNAPSHOT_NAME`.
Das Ziel ist eine neue, isolierte SQLite-Archivdatenbank, nicht das Live-Schema.
Archivdaten können keine Dashboard-Berechtigungen vergeben. Manifest, Teile,
Zeilen-/Tabellenzahlen und Archivindex werden tatsächlich geprüft.

`app.raw_archive.wav_reconcile --manifest PRIVATE_MANIFEST.json --output
NEW_PRIVATE_DIRECTORY --quarantine PRIVATE_REGISTER.json` gleicht den festen
Bestand vollständig mit Archivbelegen ab. Neuere Eingänge sind keine rückwirkende
Erweiterung dieses Bestands. Ein Belegabgleich ist kein neuer physischer Rücklesetest.
Vor jeder späteren Löschung werden die konkreten WAV-Bytes erneut geprüft.

`app.raw_archive.wav_verify --output NEW_PRIVATE_DIRECTORY` prüft unter dem
Dienstkonto die freigegebenen SQL-Spalten sowie jeweils eine begrenzte Gateway-
und Direct-WAV durch Original-/Archiv-SHA-256 und vollständiges WAV-Dekodieren.
Snapshot-Verzeichniszugriff ist kein Provider- oder Wiederherstellungsnachweis.

## Ressourcen und Rücknahme

Separate Prüfläufe: 128 MiB RAM, kein Swap, 10 % CPU, niedrige I/O-Priorität,
640 MiB Hostreserve, Lastgrenze 2,4 und maximale Laufzeit 180 Sekunden.
Innerhalb der Programme gilt eine Zwei-Minuten-Grenze für Sicherung/Restore.
Bei fehlender Reserve pausieren Prüfungen statt Empfangsgrenzen zu lockern.
Für eine große vollständige Wiederherstellung ist ein getrenntes Prüfgerät
vorzusehen, falls diese Grenzen nicht ausreichen. Der aktuelle Produktionshost
hat nur knapp vier GiB RAM und bereits beträchtliche Swap-Belegung.

Das Paket wird nicht in vorhandene Empfangs-, Kopier- oder Lesedienste eingebunden.
Rücknahme: keine weiteren separaten Prüfaufträge starten; Paket und private
Nachweise behalten. Keine bestehenden Dienste neu starten oder Dateien entfernen.

## Schema-2-Löschfreigabe

Schema-1-Vollkatalogfreigaben werden vom neuen Löschpfad abgewiesen.
Das private Pilotmanifest bleibt maximal 24 Stunden gültig und auf zehn Dateien /
fünf MiB begrenzt. Sein SHA-256 pinnt zusätzlich die vollständige
`release_acceptance`: Code-Revision, Live-Reader, reale Download-/Zonen-/Jobtests,
CSV-Anzeige für 1h/7d/30d/1y, Rücknahme, tatsächlicher Downloadfortschritt trotz
Speicherschutz, vollständige Katalogwiederherstellung, Provider-Snapshot,
Bucket-Versionierung/Lifecycle und vollständigen festen Bestandsabgleich.
Die Nachweise für Downloads, Beobachtung, Abgleich, Bucket-Diagnose und Provider
werden ebenfalls mit SHA-256 festgelegt. Diese Felder sind keine automatische
Erlaubnis; sie dürfen ausschließlich aus tatsächlichen Prüfnachweisen entstehen.

Die 24-Stunden-Beobachtung muss nach der akzeptierten Veröffentlichung erfolgen.
Eine frühere Beobachtung kann eine spätere Reader-Umschaltung nicht abnehmen.
Direkt vor Entfernung werden Katalog, Features, Quellversion und Snapshot erneut
validiert. Die lokale Sieben-Tage-Reserve bleibt verpflichtend. Quarantäne und
gemeinsame Kopier-/Löschsperre bleiben erhalten. Historische Direct-Revisionen,
die nicht aktuell veröffentlicht sind, werden nun ausdrücklich abgewiesen.

## Noch erforderliche Betreiberzugänge

| Zugang | Zweck | Status |
|---|---|---|
| Vorhandenes SSH/sudo `traver` | Privates separates Prüfpaket bereitstellen | Bereits vorhanden; keine neue Identität |
| `greenmind-raw-copy` | Bestehendes SFTP und Prüfaufträge | Vorhanden; Anmeldung tatsächlich bestanden |
| `greenmind_archive_reader_33fe395` | Ausschließlich lesende SQL-Prüfungen | Vorhanden; keine Rechte erweitert |
| Regulärer MinIO-Admin-Alias | Einmalig Diagnoseidentität provisionieren | Nicht bereitgestellt; Root-Extraktion bleibt ausgeschlossen |
| Neue MinIO-Diagnoseidentität | Nur Versionierung/Lifecycle beider exakten WAV-Buckets | Vorbereitet, noch nicht angelegt |
| Hetzner-Snapshotzugang oder Betreiber-Snapshot | Providerkennung und echter Snapshot-Restore | Noch nicht bereitgestellt |
| Getrennter Storage-Box-Lesezugang | Archivreader ohne Upload-Berechtigungen | Noch nicht bereitgestellt |
| Späterer enger Löschzugang | Exakte freigegebene Objektversionen entfernen | Nicht angelegt, nicht aktiviert |

Kein neuer administrativer Benutzer, API-Token oder Cloud-Zugang wird durch diese
Programme angelegt. Löschung benötigt weiterhin eine separate menschliche
Freigabe des fertig geprüften, konkret benannten Piloten.
