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

Die 42 lokalen Bindungstests prüfen insbesondere Produktionsquellen, versteckte
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

- 117 unterschiedliche gezielte Tests bestanden; ein Sandbox-Socketfall wurde
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

Freigegebener manueller Kopierlauf – abgeschlossen

Nach ausdrücklicher Nutzerfreigabe wurden am 27.10.2026 um 16:18:42–16:19:00 UTC
exakt eine Gateway- und eine vollständige Direct-WAV archiviert: 501688 Byte WAVs,
zusätzlich 12399 Byte Parquet-Metadaten und 2926 Byte Manifeste. Sechs Dateien
wurden über den bestehenden unabhängigen RO-Unteraccount vollständig gelesen,
gehasht und inhaltlich geprüft; beide WAVs zusätzlich vollständig dekodiert.
Die erste Ausführung hatte vor jedem Upload an der Versionsbindung gestoppt:
EnvironmentFile überschrieb PYTHONPATH und die systemd-Credential-Pfade. Beide
Profile wählen jetzt das Overlay; Credential-Pfade kommen ausschliesslich aus
systemd. Zwölf zusätzliche Negativtests verhindern diese Profilkollision.
Die Fortsetzung wurde erst nach bestätigtem Null-Upload und wirksamer Prüfung
mit dem Dienstkonto ausgeführt. Keine blinde doppelte Übertragung.

Der Lauf verwendete den vorhandenen Kopierdienst mit einer vorübergehenden,
exakt begrenzten Startanweisung. Diese wurde anschliessend entfernt. Der Timer
war nie aktiviert; beide Dienste sind wieder inactive/disabled, PAUSE gesetzt,
INSTALLATION_READY entfernt. Die Rücknahme betrifft nur eigene Testdateien;
Originaldaten und archivierte Pilotdateien bleiben erhalten.

33 Ressourcensamples, 105,54 MiB cgroup-Spitze, keine high/max/OOM-Ereignisse;
192 MiB RAM und 25 Prozent CPU blieben die Dienstgrenzen. Laufdauer 17,576 s.
Der Archivstatus ist published, nicht snapshot-verified. Es gab keinen neuen
Snapshot und keine Löschfreigabe. Ein vollständiger neuer Snapshot-/SQL-Restore,
Mandanten-/Historienabnahme und unbeaufsichtigter Betrieb bleiben separate Schritte.

Fortsetzung: nachvollziehbare Direct-Warteschlange

Die neue lokale Software ergänzt `wav_pending` und `wav_pending_event` im bestehenden
SQLite-Betriebskatalog. Revisionen oberhalb von zehn WAVs oder 5 MiB werden vollständig
zurückgestellt: Identität/Revision, Anzahl, Bytes, Inhaltsfingerprint, Erstfund,
Prüfzeitpunkt und Sperrgrund bleiben nachvollziehbar. Warteschlangeneintrag und
Fortschritt des Suchlaufs werden gemeinsam gespeichert. Nachfolgende passende
Revisionen können weiterlaufen; einzelne WAV-Teile werden nicht abgetrennt.

Der Suchlauf und offene Einträge werden unabhängig fortgesetzt. Übergrössen werden
frühestens nach 24 Stunden neu aufgelöst, höchstens zehn offene Einträge je Quelle
und Durchlauf. Erst erfolgreiche Veröffentlichung entfernt einen offenen Eintrag;
sein Ereignisprotokoll bleibt bestehen. Bei 5000 offenen Einträgen hält die Suche
mit unverändertem Cursor an. Ein nur für das Restbudget zu grosser Auftrag bleibt
für den nächsten Durchlauf am aktuellen Cursor stehen. Der jeweils zuerst
verbrauchte Grenzwert – zehn WAVs oder 5 MiB insgesamt – beendet die Kopien.
Der Export prüft die Dateianzahl erneut, falls die Quelle seit der Planung abwich.

`python -m app.tiered_archive status` liefert zusätzlich `wav_backlog` mit Anzahl,
ältestem Eintrag und nächster Prüfung je Quelle; keine Quellschlüssel werden
in Routineausgaben veröffentlicht. `tick` meldet `deferred` und `pending_wavs`.
Die Warteschlange ist kein Archivnachweis und keine Löschfreigabe. Eine grössere
Revision bleibt offen, bis sie innerhalb eines separat geprüften Betriebsumfangs
vollständig archiviert werden kann. Ein Verlust des lokalen Betriebskatalogs
verlangt einen erneuten Quellsuchlauf; die Originale bleiben erhalten.

Prüfung: 130 gezielte Tests erfolgreich. Ein zusätzlicher nativer SQLite-Test auf
dem Archivserver mit künstlichen Daten bestätigte Prozessabbrüche unmittelbar
vor/nach Commit, Wiederaufnahme, unveränderten Erstfund und Kapazitätsgrenze.
Limit 64 MiB RAM / 5 Prozent CPU; gemessene Spitze 13627392 Byte, keine
memory.high/max/OOM-Ereignisse. Netzwerk und Schlüsselverzeichnis waren gesperrt;
keine Quellen-/Archivzugriffe. Nur sein neu erstelltes Testverzeichnis wurde entfernt.
Die geänderte Software ist noch nicht in die gebundenen Archivdienste übernommen.
