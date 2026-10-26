# GreenMind: versionierte Archivierung

## Aktueller Staging-Review

Für die erste Installation gilt ausschliesslich [STAGING-REVIEW-V2.md](STAGING-REVIEW-V2.md).
Sie ersetzt die frühere Installationsbeschreibung. `staging-copy.env.example` und
`schedule.example.json` beschreiben einen echten Kopierlauf mit aktuellen Rücklesungen
gegen isolierte Kopien. Snapshot-Abnahme, Backups und Produktion sind spätere Phasen.

## Verhalten

- WAVs und vollständige wissenschaftliche Metadaten bilden gemeinsame Archivpakete.
- Die ursprünglichen WAV-Pfade bleiben gültig; bestehende Archive werden weitergelesen.
- SQL-Zeilen werden als Parquet/Zstandard archiviert, einschliesslich UUID, Zeitstempel, Dezimalwerten und Binärdaten.
- Direct-Blöcke enthalten zusätzlich rekonstruierte Originalbytes, Sequenzen, Session-Konfiguration und Verweise.
- Jeder Stand besitzt unveränderliche Inhaltsprüfsummen, Manifest und dauerhaftes Journal.
- Ein Paket wird erst nach vollständiger Rücklesung aus einem nachfolgenden, providerbestätigten Snapshot bestätigt.
- Die lokale Reserve beginnt mit dieser ersten vollständigen Bestätigung: sieben Tage. Erneute Prüfungen setzen sie nicht zurück.
- Dashboard-Daten und Features bleiben mindestens 90 Tage lokal; Protokolle mindestens 30 Tage.
- Historische Diagramme, CSVs, Featurelisten und Direct-Aufnahmen verwenden die bestehenden Berechtigungen und URLs.
- Private historische Abfragen laufen über den bestehenden Lesebroker. Öffentliche APIs erhalten keine Storage-Box-Schlüssel.
- SQL-Löschungen betreffen ausschliesslich geprüfte Primärschlüssel. Nachträgliche Zeilen und Änderungen werden nicht mitgelöscht.
- Alte Löschroutinen und SQL-Pruning delegieren bei aktivierter neuer Steuerung und dürfen nicht parallel löschen.

## Programme

`python -m app.tiered_archive` bietet `inventory`, `status`, `archive-sql`, `archive-wav`, `verify`, `restore`, `backup`, `catalog-backup`, `catalog-restore`, `tick` und `evict`.

`tick` arbeitet begrenzte, private Scopes aus einer JSON-Konfiguration ab. WAV-Scans verwenden dauerhafte Cursor, SQL-Scans geschlossene UTC-Tage und Primärschlüssel. Vollständige Scans werden wiederholt, damit spätere Änderungen oder verspätete Einträge erneut archiviert werden. Der gesamte Lauf endet nach spätestens fünf Minuten. Die bestehende Copy-Koordination verhindert konkurrierende Transfers.

`verify` benötigt die echte Storage-Box-Snapshot-ID. Bei `scheduled_snapshots=true` wählt der Timer den neuesten vom Anbieter gemeldeten Snapshot; er erstellt und löscht selbst keine Snapshots. Ein täglicher Snapshotplan muss auf der Storage Box bereits eingerichtet sein. Ein vor der Veröffentlichung entstandener Snapshot bestätigt das Paket nicht.

`backup` erzeugt Schema und erlaubte Stammdatentabellen aus demselben PostgreSQL-Snapshot. Dumps werden vor dem Upload mit einem privaten 32-Byte-Schlüssel authentifiziert verschlüsselt. Diesen Schlüssel separat vom Server und der Storage Box verwahren. Der Code erstellt keine stillschweigend ungesicherten Ersatzschlüssel.

`catalog-backup` sichert Katalog und Prüfjournal in unveränderlichen Teilen. `catalog-restore` kann daraus einen leeren Katalog rekonstruieren. Die ausgegebene Wurzelreferenz und ihre Prüfsumme müssen ausserhalb des Servers erhalten bleiben. `restore` schreibt Originale und verlustfreie SQL-Zeilen ausschliesslich in einen neuen Ordner.

## Profile und Grenzen

Das bestehende geprüfte `RAW_ARCHIVE_*`-Profil liefert SFTP, schreibgeschützte Quellen, Healthchecks und Ressourcenprüfungen. Neue Variablen stehen in `tiered-archive.env.example`. Änderungen an Produktionsrollen oder deren Rechten erfolgen im separaten Deploymentreview.

SQL-Scopes benötigen `dataset`, `organization` und `start` als UTC-Zeitstempel. Zulässige Datensätze stehen ausdrücklich in `app/tiered_archive/source.py`; unbekannte Tabellen werden beim Inventar als gesperrt ausgewiesen. Aktive Nutzer, Berechtigungen, Gerätezuordnungen, WAV-Verzeichnisse und notwendige Direct-Verweise bleiben lokal. Protokolle mit nicht gesicherten externen Rohdateiverweisen bleiben gesperrt.

Direct-Locators benötigen die additive Tabelle aus `locators.sql` im Direct-Datenbankprofil. `TIERED_ARCHIVE_DIRECT_LOCATORS_ACCEPTED` und `TIERED_ARCHIVE_FEATURE_READS_ACCEPTED` bleiben bis zur erfolgreichen Abnahme der zugehörigen historischen APIs ausgeschaltet.

Ein Löschlauf benötigt zusätzlich ein privates, per SHA-256 gebundenes Aktivierungsdokument. Es bindet Umgebung, Richtlinie, laufenden Code, zugelassene Datenarten, zeitliche Gültigkeit sowie die abgeschlossene Abnahme. Für den Pilot sind die exakten Paketidentitäten vorgeschrieben. Alte Monatsausnahmen werden nicht importiert. Der initiale Durchlauf entfernt höchstens zehn WAVs beziehungsweise 5 MiB und separat eine SQL-Teilmenge. Die vorhandenen Limits für Speicher, Last, Zeit und Übertragungsrate werden beibehalten.

## Abnahme und Einführung

1. Staging-Profile und echte Datenkopien gegen das Inventar prüfen.
2. Veröffentlichung, aktuelle Rücklesung und Snapshot-Rücklesung ausführen.
3. Archive und Katalog in neue, leere Zielverzeichnisse rekonstruieren.
4. Bestehende WAV-URLs, Featurelisten, Diagramme und CSVs vergleichen.
5. Gleichzeitige Abrufe sowie fremde Organisationen/Zonen prüfen.
6. Speicher- und Verbindungsfehler sowie abgelaufene Nachweise prüfen.
7. Produktionsstand ausschliesslich mit Kopierbetrieb installieren.
8. Ab diesem unveränderten Stand mindestens 24 Stunden beobachten.
9. Aufnahmefortschritt, Rückstand und Archivdurchsatz unabhängig vergleichen.
10. Exakte Pilotpakete gemeinsam prüfen und deren Entfernung freigeben.
11. Erst nach erfolgreicher Wiederherstellung automatische Durchläufe freigeben.

Die 24-Stunden-Abnahme verlangt mindestens 280 Samples, höchstens zehn Minuten Messlücke, keine Fehler und Fortschritt beider Empfangswege. Eine gestartete Beobachtung oder lokale Tests ersetzen diesen Nachweis nicht. Aktivierungsdokumente müssen zusätzlich Archivwiederherstellung, Dashboard-Gleichheit, Mandantentrennung, Rückstandabgleich und Rollback bestätigen.

`install-staging.py` prüft das fertige Release gegen dessen Hashmanifest. Ohne `--execute` verändert es nichts. Es akzeptiert ausschliesslich Staging-Datenbanken und einen Staging-Direct-Bucket; sämtliche Löschschalter bleiben deaktiviert. Die Installation erstellt eine neue, noch nicht gestartete Staging-Einheit. Sie benötigt eine reguläre Administrator-Anmeldung und startet oder verändert keinen vorhandenen Produktionsdienst.

## Rücknahme

Die neue Staging-Einheit stoppen und die bisherigen, unveränderten Leser weiterverwenden. Paket, Belege und sämtliche Archivdateien aufbewahren. Bereits entfernte Nutzdaten werden über die geprüften Archive wiederhergestellt; ein Software-Rollback allein stellt sie nicht wieder her. Alte und neue automatische Löschung bleiben während einer Rücknahme gesperrt.

## Grenzen vor einer Produktionsfreigabe

Native Staging-Abnahme, tatsächlich konfigurierte Snapshotpläne, aktuelle MinIO-Versionierungsprüfung, belastbare Durchsatzmessung und die anschliessende 24-Stunden-Produktionsbeobachtung sind zwingend. Lokale Testnachweise werden getrennt davon dokumentiert. SQL-Zeilenlöschungen erlauben PostgreSQL die Wiederverwendung des Platzes; sie versprechen keinen sofortigen Rückgang der Dateisystembelegung.

## Getrennter historischer Leser

`greenmind-tiered-read.service` ist ein eigener Dienst mit 192 MiB RAM und 25 Prozent CPU. Er verwendet ausschliesslich den separaten, schreibgeschützten Storage-Box-Unteraccount. Er erhält weder Datenbank-Schreibrechte noch den Uploadschlüssel. Der bestehende WAV-Lesebroker und dessen Limits bleiben erhalten. Der neue private Socket akzeptiert nur denselben Betriebssystembenutzer. Im ersten Staging erfolgt ausschliesslich lokaler Zugriff; eine Produktionsweiterleitung benötigt einen eigenen Review.

`install-staging.py --read-profile ...` installiert zusätzlich einen noch nicht gestarteten Staging-Leser. Der gemeinsame Staging-Ordner erhält ausschliesslich Lesedurchgang für dessen Gruppe. Nur die atomar veröffentlichte Katalogansicht ist gruppenlesbar; der Cache gehört dem Leser, das Schreibjournal bleibt privat. Der übergeordnete Ordner muss diesen Durchgang bereits erlauben. Die Installation überschreibt keine vorhandenen Staging-Dateien oder Dienste.

Jahresansichten lesen nur die benötigten Parquet-Statistikspalten. Diese werden bei der vollständigen Archivprüfung gegen die Originalwerte geprüft. Ein Archivleser verarbeitet höchstens 2’048 passende Partitionen je Anfrage, verwendet eine begrenzte DuckDB-Instanz und bündelt gleichzeitige Dateiabrufe. Der Dateicache ist auf 512 MiB auf Disk begrenzt; zusätzlich bleiben mindestens 2 GiB Dateisystemreserve frei. Allgemeine Obergrenzen sind 500 Zeilen beziehungsweise 8 MiB kanonische SQL-Daten und 64 MiB WAV-Pakete. Im ersten Staging gelten stattdessen 1 MiB SQL-Seiten und insgesamt höchstens zehn WAVs beziehungsweise 5 MiB je Kopierlauf, je nachdem, welche Grenze zuerst erreicht wird. Die native Einhaltung der 192-MiB-cgroup-Grenze bleibt eine Abnahmevoraussetzung.

`bindings` gibt einen nicht geheimen Fingerabdruck der Quellprofile und des Archivziels aus. Aktivierungsdokument und Abnahme müssen diesen zusätzlich zum Code- und Richtlinienfingerabdruck enthalten. Ein geändertes Quellprofil oder eine andere Storage Box sperrt die Löschung.

Die zusätzliche Freigabe `TIERED_ARCHIVE_SQL_RETENTION_ACCEPTED` bleibt zunächst `false`. Eine WAV-Abnahme aktiviert keine SQL-Entfernung. Späte und doppelte Gateway-Messwerte nach einer SQL-Entfernung sowie erneute Verdichtung historischer Zeitbereiche müssen ausdrücklich abgenommen werden.

Snapshots bilden keinen unabhängigen zweiten Speicherort. Der vereinbarte Schutzumfang bleibt Storage Box plus deren Snapshots. Ein Zurücksetzen der gesamten Box ist kein Routine-Restore: benötigt werden neue Zielverzeichnisse und das gezielte Zurücklesen der geprüften Dateien. Quelle: https://docs.hetzner.com/storage/storage-box/snapshots/

## Wiederholungen und historische Nachlieferungen

`app/tiered_archive/gateway_references.sql` wird vor der SQL-Abnahme separat in der Gateway-Staging-Datenbank installiert. Die administrativ verwalteten, kompakten Identitätsverweise bleiben lokal erhalten. Dadurch bleibt ein bereits entfernter Gateway-Messwert auch unter einer neuen Upload-ID ein Duplikat. Derselbe transaktionale Bereichsschutz umfasst sowohl die Löschung als auch gleichzeitig eintreffende Wiederholungen; aktuelle Messwerte benötigen diese historische Sperre nicht.

Verdichtete Gateway-Zeitfenster behalten einen kompakten Referenzstand mit den ursprünglichen Zeitidentitäten. Neue historische Messwerte werden damit vereinigt. Die lokale API liefert nur den noch nicht archivierten Anteil; der Archivleser ergänzt den bestätigten Bestand. Eine spätere vollständige Archivversion ersetzt den älteren Stand derselben Identität. Auch die WAV-Statistiken berücksichtigen bereits archivierte Anteile. Diese Referenzen zählen zu den weiterhin benötigten Betriebsmetadaten und werden nicht durch die 90-Tage-Regel entfernt.

Während eines noch nicht abgeschlossenen Löschvorgangs melden betroffene Archivabfragen einen vorübergehenden Fehler. Sie liefern keine scheinbar vollständige, leere Historie. Nach einem Prozessabbruch führt derselbe dauerhafte Löschauftrag den Übergang fort.

Der Ablauf plant verschlüsselte Schema-/Stammdatensicherungen beider Quellen alle zwölf Stunden, wenn `backups` im Zeitplan angegeben ist. Vor jeder produktiven Entfernung ist eine passende, höchstens 24 Stunden alte und aus einem echten Snapshot bestätigte Sicherung derselben Quelle und Storage Box zwingend. Die Backup-Datenbank muss dem bereits gebundenen Quellprofil entsprechen. Fehlende Sicherungen oder Schlüssel blockieren den Betrieb; es gibt keinen Ersatz mit schwächeren Anforderungen.

Für Objektlöschungen sind zusätzlich eigene Zugangsdaten erforderlich: `TIERED_ARCHIVE_GATEWAY_WRITER_S3_ACCESS_KEY`/`SECRET_KEY` beziehungsweise `TIERED_ARCHIVE_DIRECT_WRITER_S3_ACCESS_KEY`/`SECRET_KEY`. Die Kopier-Zugangsdaten werden dafür nicht ersatzweise verwendet. Datenbank-Schreibprofile heissen `TIERED_ARCHIVE_GATEWAY_WRITER_DATABASE_URL` beziehungsweise `TIERED_ARCHIVE_DIRECT_WRITER_DATABASE_URL`. Der Staging-Installer nimmt keine dieser Schreibzugänge an. Backup-Profile heissen entsprechend `TIERED_ARCHIVE_GATEWAY_BACKUP_DATABASE_URL` und `TIERED_ARCHIVE_DIRECT_BACKUP_DATABASE_URL`; sie verwenden eine Rolle für konsistente Lese-Snapshots und benötigen einen passenden PostgreSQL-Client.
