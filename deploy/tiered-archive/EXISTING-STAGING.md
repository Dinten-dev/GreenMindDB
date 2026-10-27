# Wiederverwendung des vorhandenen Stagings

Freigegebener Konfigurationsübergang vom 27.10.2026. Dieser Weg ergänzt den
separaten Neuinstallationsvertrag; `install-staging.py` wird nicht erneut ausgeführt.
Das versiegelte Basisrelease `5be542bdd4b44106b232677c53f507ca0e62e88a` bleibt unverändert.
Für die neuen Staging-Dienste ergänzt ein versioniertes Overlay ausschliesslich
`runtime.py`: geschlossene SQL-Sitzungen geben ihre Verbindung sofort frei
(`NullPool`). Ohne dies belegten kurzlebige Adapter das begrenzte Lesekonto
auch nach abgeschlossenen Anfragen weiter. 380 unveränderte Dateien sind
Hardlinks zum Basisrelease; nur die geänderte Datei belegt zusätzlichen Platz.
Overlay: `/opt/greenmind/tiered-archive/overlays/existing-staging-ae624c30fdda`.
`validate-existing-staging.py` prüft die konkrete bestehende Topologie und erteilt
keine Start- oder Löschfreigabe.

| Bestandteil | Wiederverwendete Bindung |
|---|---|
| Gateway SQL | 127.0.0.1:5433/plantdb_staging |
| Direct SQL | 127.0.0.1:5433/greenmind_direct_staging |
| Objektablage | 127.0.0.1:9002; greenmind-raw / greenmind-direct-staging-hotspot |
| Gesundheit | bestehende APIs 8001/8002 und MinIO 9002 |
| Box-Schreiber | bestehendes Hauptkonto u676312, Root greenmind-raw |
| Box-Leser | bestehender RO-Unteraccount u676312-sub1, Home greenmind-raw/ |
| Neue Archivobjekte | Namespace staging/ unter der bestehenden Archivwurzel |
| Lokaler Zustand | bestehender /var/lib/greenmind/tiered-archive/staging |

Das vorhandene Gateway-Testbucket heisst ebenfalls `greenmind-raw`. Entscheidend
sind die explizite lokale Staging-MinIO-Instanz, gebundene Containeridentität,
Staging-Datenbank und der Staging-Namespace. Produktionszugänge werden nicht als
Quellzugänge verwendet. Der Box-Schreiber besitzt weiter seinen vorhandenen
Kontoumfang; die Beschränkung auf `staging/` erfolgt in dieser Konfiguration und
Software, nicht durch einen neu angelegten Provider-Unteraccount. Der bestehende
RO-Unteraccount wird weder erweitert noch umgewidmet. Die Quellenrechte müssen
vor jedem späteren Start erneut mit dem gespeicherten Nachweis übereinstimmen.

Die bestehenden Anwendungskonten dürfen nicht in den Archivworker übernommen
werden: Gateway verwendet dort einen Superuser beziehungsweise MinIO-Root.
Deshalb wurden nur die bisher fehlenden reinen Leseberechtigungen ergänzt:
`greenmind_archive_staging_ro` (SELECT, default_transaction_read_only, keine
Superuser-/Administrationsrechte) und `gm-tiered-staging-ro` (GET/LIST auf die
beiden vorhandenen Testbuckets). Es gibt keine neue Datenbank, keinen neuen
Container und keine neue Storage Box. Bestehende Anwendungskonten bleiben gleich.

Die Direct-Kompatibilitätstabelle `direct_chunk_payload_ref` wurde leer ergänzt.
Original-Payloads werden nicht ausgelagert oder geändert. Sechs additive
Scanindizes begrenzen die vorgesehenen SQL-/WAV-Abfragen. Weitere Datensätze
benötigen ihre eigenen vorhandenen oder separat geprüften Indizes.

Konfiguration und Rücknahme

- Originalvorlagen: /etc/greenmind/tiered-archive/before-existing-staging-20261027/.
- Quelleingänge und Passwörter bleiben root-geschützt auf dem Archivserver.
- Systemd-Credentials verweisen auf bestehende Upload-/RO-Schlüssel.
- Neu erzeugte, bereits gesicherte SSH-Schlüssel bleiben unbenutzt erhalten.
- Der bereits wiederhergestellte Backupschlüssel bleibt unverändert gebunden.
- Bestehende Produktivdienste, deren Timer und Credentials bleiben unverändert.
- Neue Profile sind vollständig gebunden; PAUSE/fehlendes READY verhindern Starts.
- Neue Dienste und Timer bleiben deaktiviert; alle Lösch-/Retentionflags false.
- Kein Aktivierungsdokument für Löschung wird erzeugt.

Die minimale Rücknahme setzt die gesicherten Vorlagen wieder ein und entfernt
nur die zwei neuen Credential-Drop-ins und zwei Overlay-Drop-ins. Danach daemon-reload, kein Neustart
bestehender Dienste. Zusätzliche Leserechte können separat gesperrt werden;
Originaldaten und additive Indizes bleiben erhalten. Das Entfernen der Overlay-Drop-ins stellt die ursprüngliche Backend-Bindung
wieder her. Das Basisrelease und alle produktiven Laufzeiten bleiben unverändert.

Abnahme

Die 30 lokalen Bindungstests prüfen insbesondere Produktionsquellen, versteckte
DB-Verbindungsparameter, Superuser-/S3-Rootzugänge, fremde Box-Wurzeln,
Uploadzugänge im Leser und aktivierte Löschflags als Negativfälle.
Native Quellen- und Ressourcenprüfung ist ein getrennter Nachweis. Ein durch
Hostlast abgebrochener Versuch zählt als Schutzpause, nicht als bestandener Test.
Erfolgreiche Prüfungen werden in der Ausführungsakte festgehalten; dieses Dokument
behauptet keine vollständige Archiv-/Snapshot-/Restoreabnahme.

Die Native-Quellprüfung-v2 bestätigte den Gateway-WAV vollständig, beide
SFTP-Anmeldungen mit strikter Hostprüfung und SELECT ohne Tabellen-Schreibrechte.
Direct benötigte zusätzlich SELECT auf direct_enrollment. Die vorhandenen
Staging-Buckets sind nicht versioniert; Kopierprüfungen können weiterlaufen,
Löschtests und Produktionsfreigabe werden daraus ausdrücklich nicht abgeleitet.

Nachprüfung vom 27.10.2026

- 105 unterschiedliche gezielte Tests bestanden; ein Sandbox-Socketfall wurde
  separat mit freigegebenem lokalem Socketzugriff erfolgreich wiederholt.
- Native v3: zwölf nacheinander erzeugte Adapter, null verbleibende Verbindungen;
  das bestehende Verbindungslimit drei bleibt unverändert.
- Zwei Gateway-Featurezeilen und fünf Direct-Diagrammzeilen als Parquet geschrieben,
  vollständig zurückgelesen und anhand typisierter Inhaltsprüfsummen verglichen.
  Dies ist noch kein Storage-Box- oder PostgreSQL-Restore-Nachweis.
- Vollständige Direct-Revision 077ed8e1-ab55-4540-9743-c586f4069d81 / 601:
  eine WAV, 456044 Byte, 228000 Frames, vollständig dekodiert, alle Quellflags true.
- Der vorher gelesene Direct-Sonderfall enthält 187 Teil-WAVs. Er ist partial und
  bleibt für Löschung gesperrt. Der allgemeine Zehn-WAV-Staging-Sweep stoppt an
  solchen übergrossen Revisionen; ein manueller Pilot muss die geprüfte kleine
  Revision ausdrücklich wählen. Vor unbeaufsichtigtem Direct-Betrieb bleibt eine
  nachvollziehbare Warteschlange für übergrosse Revisionen erforderlich.
- Native Speicherhöchststände: Gateway/SFTP 105,3 MiB; Direct/Parquet 96,1 MiB;
  vollständiger Direct-Einzeltest 65,7 MiB. Keine high/max/OOM-Ereignisse. Dies
  belegt diese Prüfungen, noch keinen vollständigen Kopier-/Snapshotzyklus.
- Abschlussprüfung: 381 Overlaydateien unverändert, root-eigen und schreibgeschützt;
  Basisruntime unverändert. Beide neuen Dienste und Timer inactive/disabled,
  keine Startzeit, PAUSE vorhanden und INSTALLATION_READY weiterhin nicht vorhanden.
- Beide bestehenden Staging-Datenbanken und MinIO laufen seit ihren bisherigen
  Startzeiten. Rund 3,45 GiB nutzbar frei; 2 GiB Reserve bleibt vorgeschrieben.

Der vollständige Betriebsnachweis erfordert weiterhin den ausdrücklich
freigegebenen manuellen Kopierlauf, anschliessend Snapshot-/Box-Restore der neuen
Formate, vollständigen SQL-Restore, Historien-/Mandantenabnahme und Beobachtung.
Vorhandene frühere Abnahmen und Apple-Schlüsselwiederherstellung bleiben gültige
historische Belege; sie werden weder neu eingerichtet noch als neue Tests ausgegeben.
