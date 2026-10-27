# Begrenzte native Staging-Restore-Abnahme

Dieses Werkzeug prüft die beiden **bereits vorhandenen** kleinen Staging-Kopien.
Es startet weder den Archivwriter noch den Archivleser oder deren Timer. Es ist
kein produktiver Gesamtbackup-Ersatz und noch kein Teil des periodischen Workers.

## Umfang

- Quelle ausschliesslich `plantdb_staging` oder `greenmind_direct_staging`,
  bestehende Rolle `greenmind_archive_staging_ro`, localhost:5433.
- Quelle kleiner 64 MiB; Dump und binärer Inhaltsnachweis je maximal 64 MiB.
- Konsistenter vollständiger PostgreSQL-Dump mit exportiertem Snapshot,
  einschliesslich Timescale-Konfiguration und komprimierter Nutzdaten.
- Bestehender Backupschlüssel via systemd LoadCredential; verschlüsselter Dump
  wird vollständig authentifiziert, bevor pg_restore ihn verwenden darf.
- Alle öffentlichen Tabellen werden nach Primärschlüssel sortiert im nativen
  PostgreSQL-Binärformat verglichen. Ein Tabelleninhalt ohne Primärschlüssel
  wird nicht stillschweigend ausgelassen.
- Zusätzlich: Spalten, Constraints, Indizes, Views, Erweiterungsversionen,
  Hypertabellen, Dimensionen, Chunk-Zuordnung, Kompression und Sequenzstände.
- Sequenzzustände werden vor/nach Aufnahme verglichen, da Sequenzen nicht dem
  exportierten MVCC-Snapshot folgen. Änderungen sperren die Abnahme.
  Ein zusätzlich verschlüsselter Metadatenteil enthält diese Zustände, den
  Inhaltsnachweis, den Policy-Nachweis und die Prüfsumme des vollständigen Dumps.
  Das behebt auch den im normalen Dump fehlenden internen Timescale-Zähler
  `bgw_job_stat_history_id_seq`. Im eigenen Ziel werden die authentifizierten
  Zählerstände nach Prüfung derselben Sequenzdefinitionen gesetzt; anschliessend
  muss der gesamte Vergleich unverändert bestehen.
- Kopierte benutzerdefinierte Timescale-Jobs werden zunächst auf Übereinstimmung
  geprüft. Danach werden ausschliesslich im eigenen Restore-Ziel alle Jobs
  deaktiviert, bevor der Wiederherstellungsmodus beendet wird.
- Keine Box-Verbindung, keine neuen Konten, keine globalen Rechteänderungen.
  Der Test braucht nur bestehende SELECT-Rechte der ausdrücklich freigegebenen
  Staging-Objekte. Fehlende Rechte werden nicht automatisch vergeben.

## Privilegientrennung

Die kurze administrative Vorbereitung erstellt eine UUID-benannte Datenbank
mit einer zufälligen Markierung und protokolliert OID/Eigentümer vor dem Test.
Nur dort werden Timescale eingerichtet, interne Restore-Rechte vergeben und
`timescaledb_pre_restore()` ausgeführt. Der Worker selbst bleibt ohne
Superuser-, Rollenverwaltungs-, Datenbankerstellungs- oder BypassRLS-Rechte.

Nach erfolgreichem pg_restore und Policy-Vergleich fordert der Worker über
eine lokale Markierung die Nachbereitung an. Der Supervisor prüft die exakte
Zieldatenbank erneut, deaktiviert deren kopierte Jobs und ruft
`timescaledb_post_restore()` ausschliesslich dort auf. Anschliessend prüft der
Worker über eine neue Verbindung die tatsächlichen Inhalte. Komprimierte
Werte dürfen nicht schon im noch aktiven Restore-Modus bewertet werden.

Nach gestopptem Test werden nur die frisch protokollierte Datenbank und der
eigene temporäre Ordner entfernt. Kurz nachlaufende Client-Verbindungen bekommen
eine begrenzte Wartefrist. Nur der eindeutig zugeordnete eigene Timescale-Scheduler
darf nötigenfalls beendet werden. Unbekannte Sitzungen oder Identitätsabweichungen
lassen die Testdaten zur kontrollierten Bereinigung stehen.

## Ressourcen und Paketierung

Worker einschliesslich PostgreSQL-Clients: 192 MiB MemoryMax, 160 MiB MemoryHigh,
kein Swap, 25 % CPU. Inhaltsprüfung spätestens alle 64 KiB oder nach 250 ms
am nächsten COPY-Block, zusätzlich an Tabellen-/Prozessgrenzen. Das Bytebudget
gilt für jeden Block. Laufzeitgrenze 120 Sekunden; äussere Einheit maximal 150
Sekunden. Bestehende Gesundheits-, Latenz-, Speicher- und Lastgrenzen bleiben.
PostgreSQL bleibt in seinem bereits bestehenden separaten 320-MiB-Container.

Lokal erzeugt `package.py gateway <neuer-absoluter-Pfad>` beziehungsweise
`package.py direct <neuer-absoluter-Pfad>` ein versioniertes Prüfskript mit
Worker-Prüfsumme. Vorhandene Dateien/Symlinks werden nicht überschrieben. Die
Paketierung führt nichts auf dem Server aus. Das Paket wird über den bestehenden
administrativen Transport nach Prüfung seines genauen Umfangs ausgeführt.
Es enthält keinen Schlüssel oder Quellpasswort; Bindungen werden erst auf dem
Server aus dem bestehenden privaten Profil gelesen.

## Abnahmegrenzen

Ein erfolgreicher lokaler Restore beweist noch keinen Storage-Box-Upload,
Snapshot-Restore, MinIO-Objektrestore, historischen API-/Mandantenvergleich oder
24-Stunden-Gesamtbetrieb. Das installierte alte Schema-/Stammdatenformat wird
hierdurch nicht automatisch zu einem Timescale-kompletten Archivformat.
Dessen Integration, versionierter Nachweis und Box-Rücklesung bleiben erforderlich.
Für grosse Produktionsquellen braucht es weiterhin die geplante begrenzte,
partitionierte Archivierung; die 64-MiB-Staging-Grenze wird nicht angehoben.

Referenz: https://www.tigerdata.com/docs/deploy/self-hosted/backup-and-restore/logical-backup
