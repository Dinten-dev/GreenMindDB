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
Die geänderte Software wurde anschliessend als schreibgeschütztes Staging-Overlay
installiert, ohne einen Archivdienst zu starten:
`/opt/greenmind/tiered-archive/overlays/staging-queue-54a00f1bc886`.
Software-/Bindungscommit: `53ae2d0f07dcaa3b838d3175bd7b163b32aa1701`.
Runtime-SHA-256: `54a00f1bc886511c35f3b3634f187dca427fda9833ffc8aa501e53e8903cd2e2`.
Manifest-SHA-256: `415de505ec463231203691f0dce85c2164e404a5723dffe3b4eb77fe5582d42b`.
382 Dateien, davon 377 unveränderte Hardlinks; vorherige Version bleibt erhalten.
Nur die beiden PYTHONPATH-Bindungen, zwei bestehende Overlay-Drop-ins und der
exakte Profilprüfer wurden aktualisiert. Quellen, Schlüssel, Konten und Dienste
wurden nicht neu eingerichtet. Alle bisherigen Startsperren bleiben wirksam.

Rücknahme dieser Aktualisierung: Die fünf gesicherten Dateien aus
`/etc/greenmind/tiered-archive/before-queue-54a00f1bc886/manifest.json` nach
SHA-256-Prüfung auf ihre dort exakt verzeichneten Ziele zurücksetzen; danach
`systemctl daemon-reload`. Keine Dienststarts. Nicht den älteren allgemeinen
Installationsrollback mit dieser gezielten Rücknahme vermischen.

Der nächste echte Kopierlauf auf dieser Softwareversion bleibt ausstehend.
Der lokale Suchlauf wird dann die Warteschlangentabellen anlegen; der native
Test hat ausschliesslich sein eigenes künstliches SQLite-Verzeichnis verwendet.

Snapshot-Zwischenstand: Der vorhandene API-Zugang erlaubte GET, verweigerte
jedoch am 27.10.2026 den einzigen journalisierten POST mit HTTP 403. Kein neuer
Snapshot wurde dadurch erstellt. Safari zeigte eine abgemeldete Hetzner-Sitzung;
die persönliche Anmeldung wurde angefordert. Es wurden keine neuen Konten,
API-Tokens oder SSH-Schlüssel angelegt und keine Rechte erweitert. Der vorhandene
Zugang reicht weiterhin zum späteren Lesen eines über die Console erstellten
Snapshots. Eine API-Erstellung wird nicht ungeprüft wiederholt.


Fortsetzung: automatische Latenzpause und Leser-Gesundheit

Der Ausgangswert wurde bei ausgeschalteten Archivdiensten mit 30 vollständigen
HTTP-200-Antworten pro vorhandener Staging-API erfasst (Intervall zwei Sekunden).
Festgelegt ist das 95. Perzentil nach Nearest-Rank: Gateway 26,860 ms,
Direct 9,362 ms und MinIO 2,479 ms. Daraus folgen unveränderte Plan-Grenzen
von 126,860 / 109,362 / 102,479 ms (`max(1.2 * Basis; Basis + 100 ms)`).
Die gesamte Antwort wird bis höchstens 64 KiB gelesen. Ein einzelner Ausreisser
pausiert nicht; drei aufeinanderfolgende Überschreitungen derselben API pausieren.

Die Referenzdatei wird anhand ihres SHA-256 gebunden und als systemd-Credential
gelesen. Sie wird nicht automatisch auf schlechtere Werte angepasst. Fehlende,
unpassende oder veränderte Referenzen blockieren den Staging-Start. Die beiden
Profile müssen dieselben lokalen APIs und denselben Baseline-Hash verwenden.
Ausgangsmessung: 64 MiB / 5 Prozent CPU, gemessene Spitze 13049856 Byte,
keine high/max/OOM-Ereignisse. Keine Quellenänderungen.

Die Zähler stehen getrennt für Kopierer und Leser in `latency-copy.json` bzw.
`cache/latency-reader.json`. Ein Dateilock serialisiert Updates. Die Pause wird
vor dem dritten gespeicherten Überschreitungszustand dauerhaft geschrieben;
Datei und Verzeichnis werden synchronisiert. Probe-Neuanlage und Prozessneustart
setzen die Zählung nicht zurück. Defekte Zustandsdateien pausieren ebenfalls.
Erfolgreich zwischengespeicherte Healthchecks zählen nicht als neue Messung.
Ein gesunder Messwert setzt nur den Zähler der betreffenden API zurück.

Der Suchlauf prüft Gesundheit vor Quellenauflösung und Seitenabfragen. Der
Kopierer prüft während der Arbeit weiter an seinen Kontrollpunkten. Der Leser
verwendet nun explizit die Staging-Gesundheitsprüfung statt der alten Ports
8120/8003/8000. Ein Nachweis bestätigte, dass seinem bisherigen Profil die
Staging-Health-Bindungen fehlten. Dieselbe gebundene Prüfung läuft vor Anfragen
und im Server-Hauptloop, auch während laufender Anfragen oder ohne neue Nutzer.
Erfolgreiche HTTP-Messungen werden höchstens fünf Sekunden zwischengespeichert;
Speicher-, Last- und Plattengrenzen werden an jedem Aufruf erneut geprüft.
Eine dauerhafte Pause sperrt Folgeläufe und weitere Leseranfragen.

Vor manueller Wiederaufnahme sind Ursache und Messzustand zu prüfen. Nach
bewusster Entfernung von `control/PAUSE` muss die Quelle wieder gesund sein;
anhaltende Überschreitung pausiert erneut. Keine automatische Wiederaufnahme,
kein Timerstart und keine Löschfreigabe entstehen aus dieser Änderung.

Der native Fehlerfall benutzte einen künstlichen HTTP-Server im privaten
Netzwerk einer kurzlebigen Testeinheit. Drei tatsächlich verzögerte Antworten
mit jeweils neu aufgebautem Probeobjekt ergaben 1/2/3 und eine persistente Pause.
64 MiB RAM / 5 Prozent CPU; Spitze 22368256 Byte, keine Speicherereignisse.
Keine Archiv-, Quellen- oder Schlüsselzugriffe; neue Testdateien entfernt.
Der erste Versuch scheiterte vor Testbeginn an einer fehlenden TCP-Socketfamilie;
für den zweiten blieb das Netzwerk isoliert, TCP wurde nur darin zugelassen.

Ein externer Alarmweg, ein echter Kopier-/Leselauf auf dem veränderten Stand und
24 Stunden Gesamtbeobachtung stehen weiterhin aus. Der ausstehende Snapshot
bleibt von der persönlichen Hetzner-Anmeldung abhängig.

Installationsnachweis der Latenzpause – 27.10.2026, 17:52 UTC

Installierter Software-/Bindungscommit: `d2161971fd7a9233a9893796209827b2bfed0713`.
Overlay: `/opt/greenmind/tiered-archive/overlays/staging-latency-69e3320d8a80`.
Runtime-SHA-256: `69e3320d8a805eec29524f2bd5020a4883ed3cc2b74c120d738b3c4819e87beb`.
Manifest-SHA-256: `a05b2e1aae43650c1d74acfb50b213650ab7e9d2cdf9b317a6e4fe82992ce31c`.
Baseline-SHA-256: `34894d9df7cac7e077f850a47277d2021110c13997d4e08da9b0e1bc7f893b90`.

Die unabhängige Abschlussprüfung bestätigte 383 root-eigene schreibgeschützte
Dateien, alle Profilbindungen und die geladenen systemd-Credentials über D-Bus.
Beide Dienste und der Timer sind inactive/disabled ohne Dienststart;
PAUSE besteht, INSTALLATION_READY fehlt, alle acht Lösch-/Abnahmeflags sind false.
Die beiden bisherigen Pilotbelege bleiben published, nicht snapshot-verified.
Bestehende Containeridentitäten und Startzeiten sind unverändert.

189 unterschiedliche gezielte Tests bestanden. Zusätzlich konnten beide
Dienstidentitäten ihre tatsächlichen Profile und das Baseline-Credential in
isolierten nativen Testeinheiten laden: 64 MiB RAM / 5 Prozent CPU, Spitzen
17874944 bzw. 18022400 Byte, keine Speicherereignisse. Diese Vorprüfung führte
keine HTTP-, SQL- oder Archivabfragen aus und verwendete keine Archivschlüssel.
Die gesonderte native Latenzprüfung bleibt der oben dokumentierte künstliche Test.

Vorprüfungen ohne die systemd-Zusatzgruppe beziehungsweise ohne die erlaubten
Schreibpfade lieferten zunächst negative Rechteprüfungen. Die korrekte Prüfung
mit den tatsächlichen Dienstgruppen und Schreibpfaden bestand; keine ACL oder
Gruppenzuordnung wurde geändert. Zwei Abschlussprüfungen mussten die Darstellung
von LoadCredential beziehungsweise das Laden deaktivierter Unit-Metadaten
berücksichtigen. Die dritte Prüfung bestand vollständig; LoadUnit startete nichts.

Gezielte Rücknahme: Die acht Ziele aus
`/etc/greenmind/tiered-archive/before-latency-69e3320d8a80/manifest.json` verwenden.
Nach Hashprüfung die fünf vorher bestehenden Dateien zurücksetzen und nur die
drei dort als neu verzeichneten Dateien entfernen; danach daemon-reload, keine
Dienststarts. Vorherige Overlays und Basisrelease bleiben erhalten.

Weitere lokale Korrektur: SQL-Wiederherstellung

Der SQL-Restore vergleicht jetzt jeden Archivteil vollständig mit den tatsächlich
gespeicherten Werten, ohne sämtliche Nutzdaten in einer zweiten RAM-Kopie zu
sammeln. Direct-Teile und Payload-Verweise werden nach Anzahl, Chunk-Zuordnung
und Werten geprüft. Bereits gefüllte Ziel- oder Direct-Zusatztabellen bleiben
gesperrt. Die Zieltabellen werden während der Transaktion exklusiv gesperrt;
Lock-Wartezeit 150 ms, einzelne SQL-Anweisungen höchstens acht Sekunden.
Ein Prüf- oder Schreibfehler rollt alle eigenen Einfügungen zurück.

Zulässig sind nur explizite lokale PostgreSQL-Ziele mit eigenem `_restore_`-Namen
und ohne URL-Zusatzparameter. Versteckte host-, hostaddr-, service- oder
dbname-Überschreibungen werden vor dem Verbindungsaufbau abgewiesen. Eine
Restore-Ergebnisanzeige mit Dateianzahl wird nicht mehr versehentlich wie ein
Veröffentlichungsbeleg behandelt; bisher konnte die CLI nach erfolgreichem
Datei-Restore an dieser Anzeige scheitern.

23 neue Tests mit echten Parquet-Dateien und isoliertem SQLite prüfen unter
anderem spätere beschädigte Teile, Trigger-Veränderungen, fremde Direct-Kinder,
Rollback und unveränderte bestehende Daten. Zusammen mit der betroffenen
Regression: 183 bestanden, ein bereits separat geprüfter Sockettest ausgenommen.
SQLite ersetzt dabei ausschliesslich PostgreSQL-SET-/LOCK-Anweisungen für diese
lokalen Tests. Es ist kein Nachweis der PostgreSQL-Sperrwirkung.

Der zusätzliche echte PostgreSQL-Test prüft Lock-Konflikt, Rollback und erneuten
Restore. Er ist noch auszuführen: Am lokalen Rechner lief kein Docker-Daemon;
kein bestehender Dienst wurde dafür gestartet. Diese Restore-Korrektur ist
noch nicht im installierten Overlay enthalten. Vor dem nächsten echten Restore
ist sie nativ zu prüfen und versionsgebunden zu installieren. Vollständiger
Schema-/Stammdaten-Restore, Direct-Objektrekonstruktion und die übrigen
Abnahmepunkte bleiben eigenständige offene Schritte.

Native SQL-Restore-Prüfung – 27.10.2026, 18:08 UTC

Die noch ausstehende PostgreSQL-Prüfung wurde mit der vorhandenen Staging-Instanz
PostgreSQL 15.10 ausgeführt. Eine eindeutig markierte, neue Testdatenbank enthielt
ausschliesslich künstliche Tabellen und Werte. Es wurden weder Quelltabellen noch
Archivobjekte gelesen. Keine zusätzliche PostgreSQL-Instanz wurde eingerichtet.

Sieben Fälle bestanden: mehrere Parquet-Teile mit exakten UUID-/Bytea-/Decimal-/
Zeitwerten; beschädigter späterer Teil; Datenbanktrigger mit Werteänderung;
veränderte Direct-Kindzeile mit vollständigem Rollback und Wiederaufnahme;
echter PostgreSQL-Lockkonflikt (55P03), leere Tabelle und erfolgreicher Folgelauf;
doppelte Identität (23505); Unterbrechung nach begonnener Einfügung mit Rollback.

Limit 192 MiB RAM / 25 Prozent CPU, 160 MiB MemoryHigh, kein Swap. Gemessene
Spitze 64090112 Byte, keine high/max/OOM-Ereignisse, Testdauer 5,717 Sekunden.
Der bestehende Staging-Healthcheck sowie die Latenz- und Laufzeitgrenzen waren
aktiv. Der separate PostgreSQL-Container behielt sein vorhandenes 320-MiB-Limit;
die 192 MiB begrenzten den Testclient, nicht den gesamten Datenbankserver.

Nach bestätigtem Testende wurde nur die eigene Datenbank entfernt: Name, OID,
zufälliger Eigentumsmarker und null aktive Sitzungen wurden vorher geprüft.
Danach wurden nur die neuen Testdateien entfernt. Container-ID und Startzeit
blieben gleich; Archivdienste und Timer blieben inactive/disabled.
Getesteter recovery.py-SHA-256:
`0ce6119b681008c40abeaeda9d296023f92d52995c4ea5242a6762fd4e50ee36`.
Der vorbereitete Profilprüfer bindet den neuen Restore-Stand an
`/opt/greenmind/tiered-archive/overlays/staging-restore-a8ae83e46a5c/backend`.
Installation und unabhängige Abschlussprüfung werden separat nachgewiesen.

Installationsabschluss des Restore-Standes

Am 27.10.2026 um 18:11 UTC wurde der nativ geprüfte Stand installiert:
Commit `59b92f1bc8e5b1fde4c6197875e478e824efb36f`,
Overlay `/opt/greenmind/tiered-archive/overlays/staging-restore-a8ae83e46a5c`.
Runtime-SHA-256: `a8ae83e46a5ca6ed7365a54a79435d42472313c603490ad5f4864a20c585b856`.
Manifest-SHA-256: `145e5a72bb5ec05157ba3d55a205d56e0e7c2fa928c68620e2b6b5157898ac2b`.
383 schreibgeschützte Dateien, davon 381 unveränderte Hardlinks. Zwei Backend-
Dateien wurden ersetzt; das vorherige Overlay bleibt vollständig erhalten.

Die unabhängige Abschlussprüfung bestätigte den Runtime-Fingerprint, alle
Dateien und die Übereinstimmung von recovery.py mit dem nativen Test. Die vier
bestehenden Container haben unveränderte IDs, Startzeiten und Speicherlimits.
Beide Pilotbelege bleiben published. Keine Archivdienste oder Timer wurden
gestartet; inactive/disabled, PAUSE, fehlendes INSTALLATION_READY und alle acht
false-Lösch-/Abnahmeflags wurden erneut bestätigt. Latenz-Baseline und ihre
systemd-Credential-Bindung blieben erhalten.

Gezielte Rücknahme: Nur die fünf exakt verzeichneten Dateien aus
`/etc/greenmind/tiered-archive/before-restore-a8ae83e46a5c/manifest.json`
nach Prüfung ihrer Backup-Hashes zurücksetzen und daemon-reload ausführen.
Keine Dienststarts, keine Datenrücknahme und keine Entfernung alter Overlays.

Der Snapshot bleibt ausstehend. Safari zeigte erneut die Hetzner-Anmeldeseite;
die gespeicherte bestehende Anmeldung war verfügbar. Die automatische
Freigabeprüfung blockierte jedoch das Absenden des Agenten-Logins, weil zuvor
eine persönliche Anmeldung verlangt wurde. Es gab keinen Umgehungsversuch;
die persönliche Anmeldung wurde erneut angefordert. Keine Zugangsdaten wurden
ausgegeben, keine Konten angelegt und keine Zugriffsrechte erweitert.

Schema-/Stammdaten-Wiederherstellung – 27.10.2026

Die fehlende vollständige Rückspielroutine wurde ergänzt. Neue Sicherungen
berechnen ihre typisierten Stammdaten-Prüfwerte innerhalb desselben exportierten
PostgreSQL-Snapshots wie Schema- und Daten-Dump. Der bestehende verschlüsselte
Dump-Aufbau bleibt erhalten; der Parquet-Index enthält zusätzlich die Prüfwerte.
Die Bedeutung von Altarchiven und die klare Trennung zwischen Datei-Restore und
neuer vollständiger Werteabnahme sind im README dokumentiert.

Die Bestandsprüfung zeigte, dass pg_dump, pg_restore und psql auf dem Archivhost
fehlten. Die tatsächlich laufende PostgreSQL-15.10-Instanz enthält diese Programme
unter Alpine Linux. Ihre vorhandenen Binärdateien, der musl-Loader und die über
ldd ermittelten Bibliotheken wurden anhand ihrer SHA-256-Werte in eine separate
root-eigene schreibgeschützte Laufzeit übernommen. Keine neue Datenbankinstanz,
keine globale Paketinstallation und keine Docker-Berechtigung für den Worker.
Die Clientprozesse laufen nativ innerhalb derselben begrenzten cgroup wie Python.

Clientlaufzeit:
`/opt/greenmind/tiered-archive/clients/postgresql-15.10-55bb10b9c0ca`.
Manifest-SHA-256:
`55bb10b9c0cac4be01ef2d14ca03e51e3ccd837fb7ec83c2a46d4359d4b0e6f0`.
20 geprüfte reguläre Dateien, insgesamt 10762024 logische Byte. Die libc-Aliasdatei
ist ein Hardlink zum Loader. Die Versionen aller drei Programme wurden unter dem
bestehenden Dienstkonto mit 64 MiB RAM / 5 Prozent CPU und gesperrtem Netzwerk
geprüft. Nach der Profilbindung wurde diese Prüfung erneut mit der echten
EnvironmentFile durchgeführt: Spitze 12120064 Byte, keine Speicherereignisse.

Der native vollständige Test benutzte ausschliesslich künstliche Daten in einer
eindeutig markierten temporären Datenbank der vorhandenen Staging-Instanz. Die
bestehende eingeschränkte Rolle war nur Eigentümer dieser temporären Datenbank;
keine Rollenflags oder Zugriffe auf Originaldaten wurden erweitert. Die für den
Test erzeugten Verschlüsselungsschlüssel blieben temporär; der vorhandene, bereits
gesicherte Backupschlüssel wurde nicht verwendet oder geändert.

Zwölf Fälle bestanden, jeweils für Gateway und Direct: konsistenter Snapshot
trotz zeitgleicher Änderung von Stammdaten; falscher Schlüssel vor Datenbankzugriff;
Rollback des gesamten Schema-/Datenimports bei SQL-Fehler; vollständige Rückspielung
mit exakt verglichenen typisierten Werten; unverändertes gefülltes Ziel bei
Wiederholungsversuch; gesperrte Erfolgsmeldung bei einem falschen Prüfwert nach
Commit. Im letzten Fall bleibt die neue isolierte Datenbank zur Diagnose erhalten;
der Testsupervisor entfernte sie erst nach seinen unabhängigen Eigentumsprüfungen.
Die Schema-Abnahme umfasste zusätzlich Fremdschlüssel, Index und View.

Finaler nativer Lauf: 28,662 Sekunden, Spitze 88764416 Byte (84,65 MiB), keine
high/max/OOM-Ereignisse. Limit 192 MiB / 25 Prozent CPU, MemoryHigh 160 MiB,
kein Swap; die bestehende PostgreSQL-Instanz behielt separat ihr 320-MiB-Limit.
Ein erster Versuch stoppte an der Health-/Laufzeitprüfung, deren damalige Ausgabe
den konkreten Auslöser nicht unterschied. Er zählt nicht als Erfolg. Nach
bestätigtem Ende, vollständiger Bereinigung und neuer Ressourcenprüfung bestanden
die zwei folgenden Läufe; die Schutzgrenzen wurden nicht verändert.

Installiert: Commit `138fc4ac5f2434f28929567066654702129a4be3`,
Overlay `/opt/greenmind/tiered-archive/overlays/staging-fullrestore-13a7d225d975`.
Runtime-SHA-256: `13a7d225d975d8c4500d8700346439853e79404e24f237f9b62cd73ff78d45fa`.
Manifest-SHA-256: `c33ff77ee73ea04c1e952284ee280c57ed079e1c7861a6256f5fc0cda9d811ce`.
384 geprüfte schreibgeschützte Backenddateien, davon 380 unveränderte Hardlinks.
202 gezielte lokale Tests bestanden; der unveränderte Socketfall bleibt durch
seinen vorherigen separaten Nachweis abgedeckt. Lint und Diff-Prüfung bestanden.

Die unabhängige Installationprüfung bestätigte beide nativen Quellprüfsummen,
den gesamten Runtime-Fingerprint, alle Clientdateien, unveränderte bestehende
Container und weiterhin inactive/disabled Archivdienste und Timer. PAUSE besteht,
INSTALLATION_READY fehlt, alle acht Lösch-/Abnahmeflags bleiben false. Die zwei
bisherigen WAV-Pilotbelege bleiben published; es gab keinen neuen Box-Upload,
keinen Snapshot und keine Wiederherstellung echter Staging-Daten aus der Box.

Rücknahme: Die fünf exakt verzeichneten Konfigurationsdateien aus
`/etc/greenmind/tiered-archive/before-fullrestore-13a7d225d975/manifest.json`
nach Hashprüfung zurücksetzen und daemon-reload ausführen. Keine Dienststarts;
alte Overlays und Clientlaufzeit bleiben erhalten. Native Tests und Installation
ersetzen weiterhin keine Storage-Box-/Snapshot-Abnahme oder Gesamtbeobachtung.

### Historische Zeitfenster und CSV – lokale Prüfung 27.10.2026

- Archivabfragen unterstützen jetzt auch die vorgesehene Wochenauflösung.
- Originalintervalle werden vor der Anzeigeverdichtung auf Überschneidung geprüft.
- Ausgeschlossene Randwerte verändern keine Mittelwerte oder Extremwerte mehr.
- Gateway-CSV ergänzt `reading_count` als letzte Spalte.
- Bestehende CSV-Spalten und deren Reihenfolge bleiben erhalten.
- Sechs Regressionen reproduzierten beide Archivfehler vor der Korrektur.
- Zehn HTTP-Fälle vergleichen Diagramm, CSV und zusammengeführte Archivwerte.
- Geprüfte Zeiträume: 24 Stunden, sieben, 90, 365, 3650 Tage.
- Jahresabfragen bestanden zusätzlich mit vier parallelen lokalen Abrufen.
- Abgewiesene Anfragen erreichen den Archivleser nicht.

Diese Prüfung verwendet künstliche Parquet-Dateien, den echten Ledger-/Cache-/
DuckDB-Lesepfad und echte HTTP-Routen. SQL und Anmeldung sind Testadapter.
Sie ersetzt weder native PostgreSQL-Vergleiche noch die reale Mandantenabnahme.
Die Retention-Altersgrenzen werden durch gewählte Abfragezeiträume nicht geprüft.
Der bisher installierte Stand bleibt unverändert; diese Korrektur ist noch nicht
auf dem Archivserver installiert. Keine Dienste, Timer oder Löschungen gestartet.
Nachweis: `outputs/greenmind-staging-history-20261027/` im Projektverzeichnis.

### Nativer historischer Vergleich – 27.10.2026

- Bestehende Staging-Quellen ausschliesslich mit vorhandener SELECT-Rolle gelesen.
- Acht vorhandene Aggregatzeilen als lokale Test-Parquets vollständig zurückgelesen.
- Direct: sechs Zeilen, insgesamt 292600 Samples.
- Gateway: eine Messwertzeile und eine WAV-Statistikzeile.
- Gateway-Ausweichzeitraum: 07.09.2026, 12:00–12:10 UTC.
- Der zunächst vorgesehene Gateway-Zeitraum enthielt keine Aggregate.
- Je Datensatz sieben Zeitfenster plus fremde Organisation/Sensor geprüft.
- Insgesamt 24 bestandene Vergleiche gegen native PostgreSQL-Aggregation.
- Je Testdatei nur ein Download in den lokalen Cache.
- Grenzen: 192 MiB RAM, 25 Prozent CPU, kein Swap.
- Speicherhöchststände: 112721920 beziehungsweise 73977856 Byte; keine Speicherereignisse.
- Nur eigene Testdateien nach bestätigtem Testende entfernt.
- Container, Archivledger, Profile, Startsperren und Timer blieben unverändert.

Der Testkatalog ist ausschliesslich ein temporärer Adapter; keine echte
Archivquittung wurde auf `evicted` gesetzt. PostgreSQL blieb in REPEATABLE READ
und READ ONLY. Keine Box-Verbindung und keine Änderung von Originaltabellen.
Der Vergleich belegt korrekte Projektion dieser kleinen vorhandenen Datensätze,
nicht eine vollständige Jahreslast, API-Anmeldung oder Mandantenkonfiguration.

Neue vorgesehene Bindung: `/opt/greenmind/tiered-archive/overlays/staging-history-8ace20b9064a`.
Runtime-SHA-256: `8ace20b9064ac33b2e835a1a82854c8f431ebb1af8f627f156564ba03356297b`.
Vorheriges Overlay und Konfiguration werden zur Rücknahme erhalten. Installation
und unabhängige Nachprüfung werden separat in der Ausführungsakte nachgewiesen:
`outputs/greenmind-staging-history-native-20261027/` im Projektverzeichnis.

Die vorgenannte History-Bindung wurde anschliessend installiert und unabhängig
geprüft: 384 root-eigene schreibgeschützte Dateien, 382 unveränderte Hardlinks,
Manifest-SHA-256 `04374c61eca2782602550908a4d4fac12f1bfa2533124d795e79cabff62661b1`.
Rücknahme: `/etc/greenmind/tiered-archive/before-history-8ace20b9064a/manifest.json`.
Fünf exakte Vorgängerkonfigurationen sind dort erhalten. Keine Dienststarts;
Container, Archivledger, Startsperren und deaktivierte Löschschalter bestätigt.

### Snapshot der bestehenden zwei Pilotpakete – 27.10.2026

Nach persönlicher Anmeldung wurde Snapshot `1528896` auf Box `658321` erstellt:
`2026-10-27T19-03-50`, Beschreibung `GreenMind-Staging-Zwei-WAVs-20261027`.
Alle sechs vorherigen Snapshots blieben erhalten. Keine Rotation oder Rücksetzung.
Die vorhandenen zwei WAVs samt zwei Parquets und zwei Manifesten wurden daraus
vollständig zurückgelesen und geprüft: 517013 Byte insgesamt, davon 501688 WAV-Byte.
Gateway 22800 und Direct 228000 Frames, jeweils 380 Hz, mono, 16 Bit.
Provideridentität vor und nach der Rücklesung per bestehendem GET-Zugang bestätigt.
Der vorhandene primäre SFTP-Zugang wurde nur für Snapshot-GETs verwendet;
keine Erweiterung des RO-Unteraccounts und kein zusätzlicher Upload.

Finaler begrenzter Test: 5,904 Sekunden, 41385984 Byte Speichermaximum,
keine Speicherereignisse, eigene Testdateien nach bestätigtem Ende entfernt.
Die vollständige Diagnose ist unter `outputs/greenmind-staging-snapshot-20261027/`
gesichert. `Snapshot-Pruefnachweis.json` bindet API-Nachweise und Dateiresultate
per SHA-256. Der eigentliche Archivledger wurde bewusst nicht umgeschrieben:
beide Belege bleiben `published`; keine Löschfreigabe. Die reguläre Pipeline
muss gemeinsame aktuelle Nachweise vor jedem späteren Löschlauf selbst führen.
Vollständiger DB-/Direct-Restore und Gesamtabnahme bleiben getrennt offen.

### Stammdatenumfang und vorhandene Rechte – 27.10.2026

Die lesende Bestandsaufnahme bestätigt bereits vorhandene Tabellen für
`zone_access`, `plant_sensor_assignment` und `plant_observation_access`.
Der bisherige Gateway-Backupumfang liess diese Rechte und Zuordnungen aus.
Neue Sicherungen verwenden daher Nachweisversion 2 und zehn Gateway-Tabellen:
`alembic_version`, `organization`, `users`, `zone`, `gateway`, `sensor`, `plant`,
`zone_access`, `plant_sensor_assignment`, `plant_observation_access`.
Direct bleibt bei `direct_device`, `direct_enrollment`, `direct_session`.
Das ist ausdrücklich kein vollständiger Nutzdaten-Restore: Messwerte, Direct-
Blöcke, Betriebszustände und weitere Tabellen benötigen ihre jeweiligen Archive.

Vor dem Dump werden Tabellen, SELECT-Rechte und Fremdschlüssel-Abhängigkeiten
geprüft. Fehlende Tabellen oder Abhängigkeiten ausserhalb dieses festen Umfangs
blockieren den Lauf. Schema und Zeilen stammen weiterhin aus einem gemeinsamen
PostgreSQL-Snapshot. Versionsnummer, Tabellenmenge und vollständige typisierte
Inhalte werden beim Restore verglichen. Der Versionsnachweis ist auch im
manifestgebundenen Index enthalten; alte unvollständige Sicherungen erfüllen
keine aktuelle Löschvoraussetzung. Bestehende verschlüsselte Dateien bleiben
unverändert und über die bisherige Datei-Wiederherstellung lesbar.

Die vorhandene Rolle `greenmind_archive_staging_ro` darf unter anderem `users`,
`zone_access`, `plant_observation_access` und `alembic_version` noch nicht lesen.
Die getrennten `TIERED_ARCHIVE_*_BACKUP_DATABASE_URL`-Bindungen fehlen im
installierten Writer-Profil. Keine neue Rolle oder Schlüssel nötig allein aus
diesem Befund: zuerst den tatsächlichen erforderlichen Leseumfang klären.
Insbesondere kann ein vollständiger Schema-Dump zusätzliche Tabellenrechte
benötigen. Es wurden keine Berechtigungen erweitert und keine echten Dumps
hochgeladen. Das erste Zwei-WAV-Profil bleibt im bisherigen Umfang gesperrt.

Nachweise: `outputs/greenmind-staging-mastercoverage-20261027/` im Projekt.
Vorhandener Box-/Snapshot-Nachweis wird nicht wiederholt. Archivdienste, Timer,
Löschschalter und Originaldaten bleiben unverändert. Der abschliessende native Test bestand 13 Fälle mit künstlichen Daten:
31,533 Sekunden, 72679424 Byte Speichermaximum, keine Speicherereignisse.
Ein vorheriger Versuch wurde bereits vor der Datenbankanlage vom Ressourcenschutz
blockiert; erst nach erneut bestandener Ressourcenprüfung wurde wiederholt.
174 lokale Tests bestanden; 15 Docker-Tests wurden übersprungen und ein Broker-
Test abgewählt. Zusätzlich bestanden 22 Tests der aktualisierten Profilbindung.

Commit `1c3c1f669e10b4db2265d9c20882d4a95214c972` wurde anschliessend installiert:
`/opt/greenmind/tiered-archive/overlays/staging-mastercoverage-158bf0fc6bfb`.
Runtime-SHA-256: `158bf0fc6bfbf391f7a063a0ddd3f702bb0b8f3cdf62277934578c1a12caa4aa`.
Manifest-SHA-256: `940175e92ea0fcb37c7670ccf2298a6bcccd6b83cce2a1843fc9cedb2a28edae`.
384 root-eigene schreibgeschützte Dateien, davon 380 unveränderte Hardlinks.
Die unabhängige Installationsprüfung meldete keine Fehler. Containeridentitäten
und Startzeiten, Ledger mit zwei `published`-Paketen, PAUSE und alle deaktivierten
Schalter sind bestätigt. Es wurde kein Archivdienst gestartet.

Rücknahme über die fünf exakten Konfigurationssicherungen unter
`/etc/greenmind/tiered-archive/before-mastercoverage-158bf0fc6bfb/manifest.json`:
erst Hashes prüfen, gespeicherte Dateien und Modi wiederherstellen, dann
`daemon-reload`; keine Dienste starten. Das vorherige History-Overlay bleibt
unverändert erhalten. Echte Datenbank-Backup-Bindungen, Leseumfang und ein
vollständiger Restore aus der Box bleiben offen.

### Freigegebene Backup-Leserechte und Timescale-Befund – 27.10.2026

Nach ausdrücklicher Nutzerfreigabe wurden ausschliesslich fehlende SELECT-Rechte
für die 31 benannten vorhandenen Tabellen ergänzt: 27 in `plantdb_staging`,
vier in `greenmind_direct_staging`. Die bestehende Rolle
`greenmind_archive_staging_ro` bleibt ohne Schreibrechte, privilegierte
Rollenmerkmale oder zusätzliche Mitgliedschaften; Verbindungslimit drei und
Read-only-Vorgabe bleiben erhalten. Keine neuen Konten oder Schlüssel.

Die beiden `TIERED_ARCHIVE_*_BACKUP_DATABASE_URL`-Bindungen verwenden exakt die
vorhandenen RAW_ARCHIVE-Quellverbindungen. Der erweiterte Validator weist andere
Identitäten, Hosts, Datenbanken, Passwörter und versteckte Parameter zurück.
66 Bindungstests bestanden. Der unabhängige Servernachweis bestätigt alle
43 beziehungsweise 14 vorhandenen öffentlichen Tabellen als lesbar und weiterhin
keine Schreibrechte. Zeitplan, Leserprofil, Schlüssel, Quellengeheimnisse,
Archivledger und PAUSE blieben unverändert; keine Archivdienste gestartet.

Writer-Profil-SHA-256:
`7cb85d7e4dc43f47592f32f77057f7527f961ea9abc4fb7b0d77f0109131c4cc`.
Validator-SHA-256:
`9a6d90bc01b9c66eb4ea04a4268fe1d22df4dece191690dcd86e28395a744bc6`.
Rücknahme und alte ACLs/Profile:
`/etc/greenmind/tiered-archive/before-backup-bindings-20261027/journal.json`.
Nur die dort ausdrücklich protokollierten neuen SELECT-Rechte nach Prüfung des
unveränderten Nachher-ACL-Stands widerrufen. Bestehende Rechte nicht entfernen.

Der erneute begrenzte Schema-Lesetest lief für beide Quellen erfolgreich durch
(109565 beziehungsweise 22561 Byte). Es wurden keine Tabelleninhalte kopiert
und keine Box-Verbindungen geöffnet. Vorhandene Client-Warnungen sind damit
nicht als erledigt bewertet. Die eigenen Schema-Testdateien wurden entfernt.

Ein zusätzlicher realer Schema-Befund verhindert weiterhin die Restore-Abnahme:
Beide Quellen haben TimescaleDB 2.17.2. Gateway enthält die Hypertabelle
`sensor_reading` mit einem Chunk und aktivierter Kompressionskonfiguration;
Direct hat keine Hypertabelle. Der bisherige künstliche PostgreSQL-Test hatte
keine Timescale-Erweiterung im Testdatenbankschema und belegt diesen Pfad nicht.

Ein neuer Test mit künstlicher komprimierter Timescale-Hypertabelle reproduziert
im installierten Restore `permission denied for schema _timescaledb_internal`.
Keine Originaldaten waren beteiligt. Die Testdatenbank und ihre Dateien wurden
nach bestätigtem Sitzungsende entfernt. Die vorübergehend noch belegte zweite
Testdatenbank wurde erst nach frischer Identitäts- und Null-Sitzungsprüfung
entfernt; keine fremden Sitzungen beendet.

Offizieller Wiederherstellungsablauf und Einschränkungen selektiver Dumps:
https://www.tigerdata.com/docs/deploy/self-hosted/backup-and-restore/logical-backup
Der aktuelle Schema-/Stammdatenweg ist für Gateway damit nicht abgenommen:
Timescale-Kataloge, Chunk-Zuordnung, Kompressionskonfiguration sowie kontrollierte
Vor-/Nachbereitung müssen in den Wiederherstellungsnachweis einbezogen werden.
Eine Wiederherstellung als gewöhnliche Tabelle genügt nicht.

Der erste lokale Restore-Versuch mit den vorhandenen Direct-Stammdaten ergab
noch kein verwertbares Prüfergebnis. Eine zweite Diagnose wurde bei Last 1,174
oberhalb der bestehenden Grenze 1,0 bereits vor Teststart blockiert. Es wurde
kein Grenzwert angehoben und kein Produktiv-/Archivdienst gestartet. Diese
Versuche belegen ausdrücklich noch keinen tatsächlichen Datenbank-Restore.
Nachweise: `outputs/greenmind-staging-realrestore-20261027/` im Projekt.


### Privater systemd-Schlüssel und tatsächlicher Direct-Restore – 27.10.2026

Der bestehende Schlüssel bleibt unverändert. systemd stellt ihn dem Dienst
als root-eigene Datei mit Modus 0440 und privater POSIX-ACL bereit: ausschliesslich
root und UID 993 dürfen lesen, die Gruppe und andere Benutzer nicht. Die bisherigen
Modusbits allein verweigerten deshalb den Zugriff. Der Schlüssellader akzeptiert
jetzt zusätzlich genau diese ACL-Form für die aktuelle Dienst-UID. Andere Benutzer,
Gruppenrechte, Schreibrechte, fehlende oder ungültige ACLs bleiben gesperrt.
Die Prüfung verwendet den bereits geöffneten Dateideskriptor; Symlinks bleiben
verboten, Schlüssellänge und Eigentümerprüfung unverändert.

172 relevante Tests bestanden (ein Docker-Test bewusst nicht ausgeführt),
zusätzlich 88 Profil-/Betriebstests. Die native Prüfung nutzte denselben
Schlüssellader und den bestehenden Schlüssel über systemd LoadCredential.

Der anschliessende tatsächliche lokale Direct-Restore bestand: ein Gerät,
eine Registrierung und 13 Sitzungen wurden konsistent aus der vorhandenen
Staging-Kopie gesichert, verschlüsselt und in einer eigenen Testdatenbank
mit pg_restore/psql wiederhergestellt. Typisierte Inhaltsnachweise stimmen
für alle 15 Datensätze überein. Laufzeit 7,531 Sekunden, Spitzenverbrauch
59.801.600 Byte, keine OOM-/Speichergrenzen-Ereignisse; Limit 192 MiB/25 % CPU.
TimescaleDB 2.17.2 und das Schema sind vorhanden. Direct-Rohblöcke,
Box-Upload und Wiederherstellung aus einem Box-Snapshot sind damit ausdrücklich
noch nicht geprüft. Der Gateway-Timescale-Befund bleibt offen.

Vorbereiteter unveränderlicher Installationsstand:
`/opt/greenmind/tiered-archive/overlays/staging-credentials-83fd3279eea3`.
Runtime-SHA-256:
`83fd3279eea39f9351088699f580cce139d60f972ae8f91fd61f502d3ffddebb`.
Installations- und unabhängiger Nachweis folgen in den gleichnamigen JSON-Dateien
unter `outputs/greenmind-staging-realrestore-20261027/`. Erst ein erfolgreicher
Nachweis bestätigt die Installation. Keine Dienststarts oder Löschfreigaben.


Installationsnachweis abgeschlossen: Code-Commit
`f30693d6086b8a3afc6583191008ed3ebc7e8e2a`, Manifest
`ccc33e520120fb164dea8ad7af5cefe9c3358f655998d574e546f5aaf8c13309`.
384 root-eigene schreibgeschützte Dateien, davon 383 unverändert verknüpft.
Der unabhängige Prüflauf enthält keine Fehler. Backup-Bindungen, Schlüssel,
vier bestehende Container samt Startzeiten, Zeitplan, Ledger und PAUSE bestätigt.
Beide Archivdienste und Timer inactive/disabled, Dienststartzeitstempel leer.
Die eigene Direct-Restore-Datenbank und Testdateien sind entfernt; beendet wurde
nur ihr eindeutig zugeordneter Timescale-Scheduler nach Prüfung von OID, Eigentümer
und Testmarkierung. Keine fremden Sitzungen beendet.

Rücknahme ausschliesslich über
`/etc/greenmind/tiered-archive/before-credentials-83fd3279eea3/manifest.json`:
fünf gesicherte Konfigurationen nach Hashprüfung und mit gespeicherten Modi
zurückspielen, anschliessend daemon-reload ohne Dienststart. Voriges Overlay
und die bereits freigegebenen SELECT-Rechte bleiben dabei erhalten.


### Native vollständige Staging-Restores – 27.10.2026, anschliessende Abnahme

Das neue manuelle Prüfwerkzeug unter `native-restore/` stellt Gateway und Direct
mit ihren tatsächlichen Staging-Inhalten über vollständige pg_dump-Dateien und
separat verschlüsselte Wiederherstellungsmetadaten wieder her. Worker-SHA-256:
`a291de324d0bba240f79a8d2c9ba1fe1ab4238e93c477230de5ab572df785b78`.

Gateway: 43 öffentliche Tabellen, 110 Datensätze und zehn Sequenzstände;
Direct: 14 öffentliche Tabellen, 32373 Datensätze und elf Sequenzstände.
Alle öffentlichen Zeilen binär nach Primärschlüssel verglichen, zusätzlich
Spalten, Constraints, Indizes, Views, Erweiterungsversionen, Hypertabellen,
Dimensionen, Chunk-Zuordnung und Kompression. Ein separater künstlicher Test
bestätigt auch tatsächliche komprimierte Messwerte. Zielgebundene administrative
Timescale-Vor-/Nachbereitung behebt den zuvor reproduzierten Fehler, ohne dem
Archivbenutzer globale Administratorrechte zu geben. Kopierte Hintergrundjobs
werden vor Rückkehr in den Normalmodus ausschliesslich im eigenen Ziel deaktiviert.

Der im normalen Dump fehlende interne Timescale-Zähler
`_timescaledb_internal.bgw_job_stat_history_id_seq` wird aus authentifizierten
Zusatzmetadaten wiederhergestellt. Keine Abschwächung des Sequenzvergleichs.
Zusätzlich ausdrücklich freigegeben wurde nur SELECT auf
`greenmind_direct_staging.public.direct_budget_id_seq` für die bestehende Rolle;
OID 22784, keine USAGE-/UPDATE-Rechte. Rücknahmejournal:
`/etc/greenmind/tiered-archive/before-direct-sequence-select-20261027.json`.

Gateway 4,044 s / 43081728 Byte RAM-Spitze; Direct 6,152 s / 49606656 Byte.
Ressourcenlimits unverändert: 192 MiB / 25 % CPU / 120 Sekunden Arbeitsbudget.
Der erste Direct-Vergleich lief wegen zu häufiger Ressourcenabfragen in die
Zeitgrenze. Gebündelte Prüfungen pro 64 KiB beziehungsweise 250 ms beheben den
Aufwand; Bytegrenze weiterhin pro Block. Fünf Regressionstests bestanden.
Lastpausen und fehlgeschlagene Versuche wurden erhalten; keine Grenzwerte erhöht.

Alle eigenen Testdatenbanken/-dateien entfernt. Container/Startzeit, Profile,
Ledger und PAUSE unverändert; Archivdienste und Timer inactive/disabled.
Evidenz unter `outputs/greenmind-staging-timescale-full-20261027/` im Projekt.

Diese Abnahme ersetzt NICHT das installierte periodische Backupformat. Dessen
Timescale-komplette Integration und der Restore aus einem Storage-Box-Snapshot
bleiben offen. Es gab keine Box-Uploads; keine Produktionsabnahme oder
Löschfreigabe wird aus den lokalen Staging-Restores abgeleitet.

### Vollbackup aus Storage-Box-Snapshot – 27./28.10.2026

Der installierte Stand ist jetzt `695aca566dd0669186e1f7a169845528cdff7940`,
Overlay `/opt/greenmind/tiered-archive/overlays/staging-native-76c7fa172272`.
Die acht ausdrücklich freigegebenen Sicherungsdateien (2’638’048 Byte) wurden
nach `greenmind-raw/staging/` übertragen und vollständig zurückgelesen.
Snapshot `1529628`, Name `2026-10-27T21-27-48`, wurde genau einmal erstellt;
sieben vorherige Snapshots blieben erhalten.

Die anschliessenden Restores verwendeten ausschliesslich Snapshot-Inhalte:
Gateway 43 Tabellen / 110 Zeilen / zehn Sequenzen, Direct 14 Tabellen /
32’373 Zeilen / elf Sequenzen. Werte, Strukturen und Sequenzen stimmten
vollständig überein. Nur die eigenen temporären Restore-Datenbanken und
Testdateien wurden entfernt. Die 192-MiB-/25-Prozent-Grenzen galten für die
Prüfprozesse; PostgreSQL blieb im bestehenden separaten 320-MiB-Container.

Nachweise: `outputs/greenmind-staging-nativebox-20261027/` im Projekt.
Serverseitig liegt der manuelle Nachweis root-eigen mit Modus 0400 unter
`/etc/greenmind/tiered-archive/native-snapshot-1529628-proof.json`.
Seine Ablage am 28.10. erneuert nicht den Prüfzeitpunkt vom 27.10.
Der Löschledger blieb unverändert; daraus folgt keine automatische Freigabe.

### Objektmetadaten vor vollständigem MinIO-Restore – 28.10.2026

Die reine Bestandsprüfung fand zwei Gateway-Objekte (91’288 Byte) sowie
681 Direct-Objekte (12’250’764 Byte). Beide Staging-Buckets sind unversioniert.
Gateway wurde vollständig gelesen; für den gesamten Direct-Bestand liegt
bislang nur ein paginiertes Metadateninventar vor. Dieses ist keine atomare
MinIO-Sicherung und kein vollständiger Restore.

Der Quelladapter bewahrte bisher nur Version, ETag, Länge und Änderungszeit.
Der neue, noch nicht installierte Stand ergänzt HTTP- und Nutzermetadaten:
ContentType, ContentEncoding, ContentLanguage, ContentDisposition, CacheControl,
Expires, WebsiteRedirectLocation und Metadata. Neue Downloads vergleichen
Identität und Metadaten sowohl mit der GET-Antwort als auch mit einem
abschliessenden versionsgebundenen HEAD. Fehlende Metadaten (`MissingMeta`),
überlange Metadaten und verkürzte Datenströme sperren die Verarbeitung.
Ältere Nachweise bleiben lesbar, erlauben ohne diese Metadaten aber keine
Quelllöschung. Tags, ACLs, Bucket-Konfiguration und Versionswiederherstellung
sind damit noch nicht vollständig abgenommen.

Die neue Adapterdatei wurde isoliert auf dem Archivserver getestet:
zwei bestehende Staging-WAVs, 501’688 Byte, vollständige Dekodierung,
JSON-Rundlauf und identische Metadaten vor/nach Download. Diese Auswahl ist
nicht identisch mit dem früheren Zwei-WAV-Uploadpilot; es erfolgte nur ein
Quell-Lesetest, kein Upload und kein neuer Snapshot. 45,2 MiB gemessene
Prozessgruppenspitze; keine Speicherereignisse. Eigene Testdateien entfernt;
Profile, Katalog, Container, PAUSE und deaktivierte Units unverändert.
Nachweise: `outputs/greenmind-staging-objectmetadata-20261028/` im Projekt.

683 lokale Regressionstests bestanden, elf Docker-Integrationstests übersprungen.
Der erste breite Aufruf mit `--noconftest` war wegen fehlender Fixtures und
Sandbox-Sockets ungeeignet; der vollständige Aufruf mit regulären Fixtures
und erlaubten lokalen Testsockets bestand. Der neue Code ist damit geprüft,
aber noch kein installierter Stand und kein vollständiger MinIO-Restore.

### Installation und native MinIO-Versionierungsprobe – 28.10.2026

Die oben beschriebene Metadatensicherung ist jetzt installiert: Commit
`380e87a713bd1025180474c7f688ea7faaf1efbc`, Overlay
`/opt/greenmind/tiered-archive/overlays/staging-objects-604352b27ded`, Runtime
`604352b27ded26d3f5805570df684996dc85fcdfc3de08dff63b18c131e6a49b`.
385 Dateien wurden unabhängig geprüft; 380 unveränderte Dateien sind mit dem
vorherigen unveränderlichen Stand verlinkt. Neben der einen Laufzeitdatei
wurden vier abweichende Testdateien konsolidiert. Rücknahme unter
`/etc/greenmind/tiered-archive/before-objects-604352b27ded/`; der vorherige
Stand bleibt erhalten. Nachweise: `outputs/greenmind-staging-objectinstall-20261028/`.

Im bestehenden Staging-MinIO wurde anschliessend genau ein UUID-gebundener
Testbucket mit künstlichen WAV-Inhalten geprüft. Keine neuen Konten, keine
Originaldatenübertragung und keine Veränderung der ursprünglichen Buckets.
Eine unversioniert angelegte Testdatei blieb nach Einschalten der Versionierung
im Testbucket als `VersionId=null` adressierbar. Nach Schreiben einer neuen
Version waren beide Inhalte getrennt lesbar. Die alte Version wurde in einen
neuen Testschlüssel zurückgeschrieben: SHA-256, HTTP-/Nutzermetadaten und alle
drei künstlichen Samples stimmten überein. Die vom Ziel neu erzeugte Version-ID
wurde als Zuordnung dokumentiert; Version-IDs werden nicht als identisch behauptet.
Dies war eine sequenzielle Versionsprobe, kein Lasttest paralleler Schreibprozesse.

Alle drei erzeugten Objektversionen wurden anhand ihres exakten Schlüssels und
ihrer Version-ID im besitzmarkierten Testbucket entfernt. Danach wurde nur der
leere eigene Testbucket entfernt und seine Abwesenheit unabhängig per HEAD
bestätigt. Vorherige Versionen oder Snapshots auf der Storage Box waren nicht
beteiligt. Der bestehende Staging-MinIO-Container blieb unverändert gestartet.
Prozesslimit 64 MiB / 5 Prozent CPU, gemessene Spitze 43,6 MiB, keine OOM-Ereignisse.
Der MinIO-Server behielt sein separates bestehendes 256-MiB-Limit.

Nachweise: `outputs/greenmind-staging-minio-versiontest-20261028/`.
Die ursprünglichen Staging-Buckets bleiben unversioniert. Die synthetische Probe
belegt Versionsadressierung und Metadaten-Rückschreibung dieser MinIO-Instanz;
sie ersetzt weder den vollständigen Restore aller realen Objekte aus der
Storage Box noch dessen Versions-/Tag-/Berechtigungsabnahme. Archivdienste,
Timer, PAUSE und alle Lösch-/Abnahmeflags bleiben unverändert gesperrt.

### Zwei echte WAVs aus Snapshot inklusive Objektattributen – 28.10.2026

Für die zwei bereits archivierten Pilotaufnahmen wurden die fehlenden
Objektattribute einmalig ergänzt: zwei Dateien, zusammen 4’753 Byte,
Attributinhalt mit dem bestehenden Backupschlüssel verschlüsselt. Die
Quellidentitäten und SHA-256 stimmten bei der Vorbereitung mit den alten
Archivnachweisen überein. Keine WAV wurde erneut auf die Storage Box geladen.
Upload und vollständige unabhängige Rücklesung bestanden. Der zusätzlich
freigegebene manuelle Snapshot `1531446` (`2026-10-28T08-44-05`, Beschreibung
`GreenMind-Staging-WAV-Attribute-20261028`) wurde erstellt; alle acht vorherigen
Snapshots blieben erhalten.

Der Restore las ausschliesslich diesen Snapshot: sechs bestehende Archivteile
und die zwei Ergänzungsdateien. Verschlüsselungsauthentifizierung, Manifest-
und Parquet-Inhalte, Quellidentitäten und vollständige WAV-Dekodierung wurden
geprüft. Beide WAVs wurden in einen eigenen, vorher nicht vorhandenen,
markierten Staging-Testbucket mit aktivierter Versionierung zurückgeschrieben
und versionsgebunden vollständig zurückgelesen. Gateway: 45’644 Byte und
22’800 Samples; Direct: 456’044 Byte und 228’000 Samples. Jeweils 380 Hz,
mono, 16 Bit. SHA-256, HTTP-/Nutzermetadaten, Tags und native ACLs stimmten.
Beide Aufnahmen haben keine Tags und jeweils einen CanonicalUser-FULL_CONTROL-
Eintrag. Diese MinIO-Version liefert eine leere Owner-ID und keine Grantee-ID;
die vollständigen nativen ACLs wurden mit Zielbucket und Zielobjekten exakt
verglichen. Die neu erzeugten Version-IDs sind im Nachweis zugeordnet; alte
Version-IDs und Änderungszeiten bleiben Archivmetadaten.

Vor dem erfolgreichen Lauf scheiterten Prüfhelfer schreibfrei an fehlendem
PYTHONPATH für den SFTP-Unterprozess und an der MinIO-ACL-Darstellung.
Fehlversuche und lesende ACL-Diagnose bleiben dokumentiert. Erst der vierte
Restore-Helfer erreichte MinIO-Schreiboperationen. Ausschliesslich dessen zwei
exakt protokollierte Testversionen wurden entfernt, anschliessend der leere
eigene Bucket; HEAD bestätigte seine Abwesenheit. Eigene lokale Testdateien
wurden entfernt. Es gab keine Quellzugriffe während der Restore-Läufe,
keine Katalogmutation, keinen Dienststart und keine Löschfreigabe.

Erfolgreicher Lauf: 7,822 Sekunden, Prozessgruppenspitze 64,44 MiB, Limit
192 MiB RAM / 25 Prozent CPU, keine Speicherereignisse. Der bestehende
MinIO-Server behielt sein separates 256-MiB-Limit. Profile, Katalog, Container,
PAUSE und deaktivierte Dienste/Timer blieben unverändert.

Nachweise: `outputs/greenmind-staging-wavrestore-20261028/`, insbesondere
`Restore-Ergebnis-v4.json`, `Abschlusspruefung.json` und `Snapshots-nachher.json`.
Dauerhafter Servernachweis:
`/etc/greenmind/tiered-archive/wav-restore-83ef79ef947d49e2a7bb5f9682393ac6-v4-result.json`,
SHA-256 `519287a6d691b9e4ca969bcea4ecee474950c8711fafc36772d3c895d065d116`.

Dieser abgegrenzte Restore ist bestanden. Er ist keine vollständige
Staging-/Produktionsabnahme: die übrigen realen MinIO-Objekte, historische
API-/CSV-/Mandantenvergleiche, Fehlerfälle, externer Alarmweg, Durchsatz und
24-Stunden-Betrieb bleiben gesondert nachzuweisen. Löschautomatik bleibt aus.

### Vollständige WAV-Inhaltsprüfung und native Historienvergleiche – 28.10.2026

Der gesamte vorhandene Staging-MinIO-Bestand wurde jetzt vollständig gelesen:
683 WAV-Objektversionen, 12’342’052 Byte. Gateway: zwei Objekte/91’288 Byte;
Direct: 681 Objekte/12’250’764 Byte. SHA-256 und vollständige Sample-Dekodierung
bestanden für jedes Objekt. Inventare vor/nach der Lesung und objektgebundene
HEAD-/GET-/HEAD-Nachweise waren stabil. Beide Quellbuckets bleiben unversioniert.
Keine Boxzugriffe, neuen Konten oder Originaländerungen. 54,222 Sekunden,
47,68 MiB cgroup-Spitze, Limit 192 MiB/25 Prozent CPU, keine Speicherereignisse.

Die begrenzte, wiederverwendbare Prüfung ist als `object-audit.py` abgelegt.
Sie prüft alle Inventarseiten, alte konkrete Versionen, Abbruchgrenzen und
kurze/beschädigte WAVs. Native Protokollunterschiede sind berücksichtigt:
LIST enthält Millisekunden, HTTP-HEAD ganze Sekunden; ein leerer Versionscursor
ist in dieser unversionierten MinIO-Instanz zulässig, wenn der Schlüssel fortschreitet.
Beide Zeitangaben bleiben im Nachweis erhalten. Zwölf gezielte Tests bestanden.
Nachweise: `outputs/greenmind-staging-full-objectaudit-20261028/`.
Dies ersetzt weiterhin kein vollständiges Box-Backup/Restore aller 683 Objekte.

Für Diagramme und CSVs wurden echte Staging-Daten beider Quellen verwendet:
zwei `visual_reading`-, zwei `visual_wave`- und 42 `direct_visual_point`-Zeilen.
Der fehlende Index `tiered_visual_reading_scan` wurde additiv in
`plantdb_staging` ergänzt. Vorher/nachher waren alle drei Tabellenzeilen samt
Inhaltsprüfsumme identisch; keine Berechtigungs- oder Messwertänderung.

Die echte HTTP-/JWT-/Mandantenprüfung lief im kurzlebigen Prüfprozess, ohne
Listener/Dienststart. Vorhandene aktive, bestätigte Benutzer aus zwei Firmen
wurden mit einem ausschliesslich im Prüfprozess erzeugten Signierschlüssel
geprüft. Keine Anmeldepasswörter oder bestehenden JWT-Schlüssel verwendet.
Die Staging-SQL-Verbindungen waren read-only, pro Datenbank auf eine gepoolte
Verbindung begrenzt. Dagegen wurden lokale Parquet-Dateien mit simuliertem
Archivbesitz verglichen; die wirklichen Quellen wurden dabei nicht entfernt.

Dabei wurde eine reale Parallelblockade aufgedeckt: synchrone DB-Abfragen in
der asynchronen Authentifizierungsabhängigkeit blockierten die Ereignisschleife,
während ein zweiter Aufruf auf die begrenzte Verbindung wartete. Die Abhängigkeit
läuft jetzt synchron in FastAPIs Workerthread. Der Fehler ist lokal reproduziert;
der Regressionstest sowie der native Parallelzugriff bestehen. Direct verwendet
für die Standardabweichung jetzt dieselbe zweite Originalmoment-Berechnung wie
der Archivleser, ohne das unnötige Rückquadrieren des zuvor berechneten RMS-Werts.

Acht native Bereichsfälle bestanden: Gateway/Direct, jeweils 1, 7, 90 und
365 Tage, Diagramm/CSV, Ablehnung fremder Firmen und anonymer Aufrufe.
Abgelehnte Aufrufe erreichten den Archivleser nicht. Je Quelle zusätzlich vier
Jahresansicht-/CSV-Anfragen mit zwei gleichzeitigen Nutzern bestanden.
Typisierte Archivoriginale, Zeitstempel, Anzahlen, Extremwerte und gerundete
Mittelwerte wurden exakt verglichen. Nur abgeleitete Standardabweichung, RMS
und Abdeckung haben den dokumentierten Gleitkommavertrag: absolut 1e-9,
relativ 1e-12. Der grösste gemessene Unterschied war etwa 3,13e-12.
Der Ratenbegrenzer wurde nur im Prüfprozess für die vielen Testexports deaktiviert.
Diese lokale Besitzsimulation ersetzt keinen vollständigen SQL-Entfernungs- oder
Box-Lesebroker-Test nach tatsächlicher Datenentfernung.

Die 24-Stunden-Auswertung wurde zusätzlich verschärft: Ressourcenpausen,
fehlende Pflichtmessungen sowie doppelte oder ausserhalb des Messfensters
liegende Samples können keine erfolgreiche Abnahme mehr ergeben. Sechs
gezielte Auswertungstests bestehen. Insgesamt bestanden 92 relevante lokale
Archiv-/HTTP-/CSV-/Parallelregressionen, 27 Authentifizierungstests und
66 Bindungstests; diese Gruppen überlappen und sind nicht zu addieren.

### Konsolidierter installierter Stand – 28.10.2026

Codecommit `db847ed294cc59b22429392871b6ad2aa17b8c62`, Overlay
`/opt/greenmind/tiered-archive/overlays/staging-ready-55c067b4c8e5`, Runtime
`55c067b4c8e5d7aa5bf737a0d2e3299548ce620a841793b0ac8bab65044cf5f4`.
390 Dateien sind unabhängig hashgeprüft, root-eigen und schreibgeschützt;
381 unveränderte Dateien verwenden Hardlinks. Der unveränderte Vorgänger und
fünf gesicherte Konfigurationsdateien unter
`/etc/greenmind/tiered-archive/before-ready-55c067b4c8e5/` erlauben die Rücknahme.
Die Installation selbst lief begrenzt mit 192 MiB/25 Prozent CPU und benötigte
14,24 MiB Spitze. Keine Archivdienste gestartet, Timer disabled, PAUSE vorhanden,
INSTALLATION_READY weiterhin abwesend, sämtliche Lösch-/Abnahmeflags false.

Auch der tatsächlich installierte, nicht mehr injizierte Stand bestand alle
acht nativen Historienfälle: 22,689 Sekunden, etwa 124,84 MiB cgroup-Spitze,
keine Speicherereignisse. Profile, Katalog, Originale und Containerstartzustände
blieben im Prüflauf identisch. Dies ist eine native Testabnahme der beschriebenen
Pfade, kein gestarteter Archivbetrieb und kein 24-Stunden-Betriebsnachweis.

Nachweise: `outputs/greenmind-staging-readiness-20261028/` und
`outputs/greenmind-staging-readyinstall-20261028/` im Projekt. Insbesondere
`Historien-Installierter-Stand.json` und `Installationsnachweis.json`.
Externer Alarmweg, die verbleibenden nativen Fehlerfälle, vollständiges
Box-/Katalog-Restore des erweiterten Umfangs und der unveränderte 24-Stunden-
Gesamtbetrieb bleiben eigene offene Schritte. Produktionsanbindung und
Produktionsbeobachtung sind weiterhin keine erledigten Staging-Nachweise.

### Nativer künstlicher Speichermangel – 28.10.2026

Ein eigener schlüsselloser Testprozess wurde unter einer harten 64-MiB-
Speichergrenze und fünf Prozent CPU gezielt durch den Kernel beendet.
Der installierte ExecStopPost-Pfad erkannte `oom-kill`, las die tatsächlichen
cgroup-Zähler (`oom=1`, `oom_kill=1`, Spitze exakt 64 MiB), schrieb Fehlermetrik
und dauerhafte Pause ausschliesslich in den eigenen Testzustand. Ein separater
Folgelauf verweigerte seine Arbeit wegen dieser Pause; die Sentineldatei wurde
nicht angelegt. Beide Testeinheiten sind beendet, nur eigene Testdateien wurden
entfernt. Haupt-PAUSE, Profile, Katalog sowie inaktive/deaktivierte Archivunits
blieben exakt gleich. Keine Quell-/Boxzugriffe oder Credentials im Testprozess.
Der eigentliche Fehlerprozess war absichtlich nicht speichergesampelt; dieser
Nachweis belegt Kernel-OOM, Nachbehandlung und Wiederanlaufsperre, keinen
vollständigen unterbrochenen Archivzyklus.

Nachweis: `outputs/greenmind-staging-faulttests-20261028/Speicherfehler.json`.

### Native Veröffentlichung bei simulierten Transportfehlern – 28.10.2026

Zwei weitere Fehlerfälle liefen gegen den installierten Publikations-,
SFTP-Fehlerklassifikations- und ExecStopPost-Pfad: Quota erschöpft und Abbruch
während eines PUT. Ein künstlicher Clientprozess lieferte die entsprechenden
Fehler; keine Netzverbindung oder echte Box-/Quell-/Schlüsselzugriffe.
Der GET-Existenzcheck war eine explizite Fehlend-Fixture. Der fest auf
`/usr/bin/sftp` gebundene Grössenwächter wurde dadurch nicht ausgeführt und
nicht verändert; der erste ungeeignete Testaufbau bleibt dokumentiert.

Beide Fälle erzeugten einen fehlgeschlagenen Lauf, Fehlermetrik und eigene
dauerhafte Pause. Es entstanden null veröffentlichte Archivnachweise.
Die künstliche Parquet-Quelle blieb unverändert. Beim simulierten Abbruch blieb
die eigene 128-Byte-Teildatei bis zur abschliessenden Testbereinigung bestehen.
Ein jeweils eigener Folgelauf startete wegen der Pause weder Arbeit noch einen
weiteren Transportversuch. Alle Testeinheiten sind beendet, ausschliesslich
eigene Testdateien entfernt. Hauptprofile, Katalog, PAUSE und deaktivierte
Archivunits unverändert. Je 128 MiB RAM/fünf Prozent CPU; gemessene Spitzen
rund 39,67 MiB, keine OOM-Ereignisse.

Das belegt die Fehlerpfade an der Clientprozessgrenze mit künstlichen Daten;
eine tatsächliche Provider-Quota wurde nicht ausgeschöpft. Nachweis:
`outputs/greenmind-staging-faulttests-20261028/Transportfehler-v2.json`.

### Tagesarchive, Snapshot-Katalog und native SQL-Transaktionen – 28.10.2026

Der ausdrücklich freigegebene Umfang von 17 Dateien/78’492 Byte ist abgeschlossen.
Sieben Parquet-Tagesarchive enthalten zwei `visual_reading`-, zwei `visual_wave`-
und drei `direct_visual_point`-Partitionen, zusammen 46 Originalzeilen. Sieben
Manifeste, ein komprimierter Katalogteil, dessen Wurzel und ein separater
Wiederherstellungsindex vervollständigen das Paket. Vor dem ersten Box-Schreiben
wurden alle unveränderlichen lokalen Dateien geprüft; nach jeder Veröffentlichung
folgte eine unabhängige vollständige SFTP-Rücklesung mit SHA-256-Vergleich.
Bestehende WAV-, Datenbank- und Attributarchive wurden lediglich referenziert.

Nach eigenen alten Snapshot-Löschungen des Nutzers bestand die Ausgangsliste aus
`1482905,1495096,1501404,1508571,1529628,1531446`. Genau ein zusätzlicher manueller
Snapshot wurde erstellt: `1532426`, Name `2026-10-28T10-54-04`, Beschreibung
`GreenMind-Staging-Tagesarchive-20261028`. Die UI meldete zeitweise einen Timeout;
es gab keinen zweiten Erstellversuch. Sichtbare Snapshot-Liste und unabhängige
GET-API bestätigten die einmalige Erstellung. Alle sechs Ausgangs-Snapshots sind
nach dem Test weiterhin vorhanden; durch diese Arbeit wurde keiner gelöscht.

Der Restore-Prozess erhielt keine lokalen Archivnutzdaten oder Quellkatalogkopie.
Alle 17 Dateien kamen ausschliesslich per GET aus diesem Snapshot. Die 46
typisierten Messwertzeilen waren gegen bestehende read-only Staging-Abfragen
identisch. Der operative Katalog liess sich allein daraus mit seinen zwei echten
`published`-WAV-Nachweisen rekonstruieren. Der separate Wiederherstellungsindex
verknüpft zusätzlich alle sieben SQL-Manifeste sowie die bestehenden nativen
PostgreSQL- und Attributsicherungen. Die sieben SQL-Archive wurden weder in den
Hauptkatalog eingefügt noch als operativ verifiziert oder entfernt ausgegeben.

Mit diesen echten Snapshot-Parquet-Dateien bestanden die acht nativen
HTTP-/CSV-/JWT-/Mandantenfälle beider Quellen für 1/7/90/365 Tage, dazu je Quelle
vier parallele Jahresansicht-/CSV-Aufrufe. Abgelehnte Aufrufe erreichten keinen
Archivabruf. Archivbesitz wurde ausschliesslich im Prüfprozess simuliert; es gab
keine tatsächliche Quellentfernung und keinen gestarteten Archivlesedienst.
Der bekannte Gleitkommavertrag blieb absolut 1e-9/relativ 1e-12, maximale
abgeleitete Differenz etwa 1,947e-12. Originalwerte und übrige Vergleichsfelder
waren exakt. Laufzeit 36,813 Sekunden, cgroup-Spitze 123,367 MiB; Grenze
192 MiB/25 Prozent CPU, keine Speicherereignisse.

Zusätzlich bestanden neun native SQL-Fälle in genau einer eigenen Datenbank
mit ausschliesslich künstlichen Werten: typisierter Export bei weiter gesperrter
Retention, geänderte Werte, NOWAIT-Zeilensperre, atomarer Transaktions-Rollback,
Abgleich nach bereits erfolgtem Commit, verspätete Aggregation als Delta,
wartende Wiederholung gegenüber erhaltenen Identitäten, geänderte Direct-
Revision und geänderte Direct-Werte. Eigene Datenbank und Testdateien sind
entfernt; Laufzeit 5,195 Sekunden, Spitze 60,531 MiB, keine Speicherereignisse.
Das sind echte PostgreSQL-Transaktionen mit künstlichen Daten; weder eine
SQL-Löschfreigabe noch der vollständige Löschcontroller mit Prozessabsturz wurden
ausgeführt. Diese zusätzliche Prozessabsturz-Abnahme bleibt offen.

Der bestehende 389-MiB-Altkatalog wurde ausschliesslich per indizierter Identität
und einem begrenzten 64-Zeilen-Präfix für bekannte Bucketnamen gelesen. Es gab
keinen Vollscan, Quell- oder Boxzugriff. Null Alt-Identitäten passten; der neue
Katalog lieferte drei reine Inhaltskandidaten. Einer ist die zweite Gateway-Datei
mit identischen WAV-Bytes. Das belegt keine dritte archivierte Objektversion und
auch nicht das Fehlen anderer Box-Dateien. Zwei genaue WAV-Restores bestehen;
weitere 681 Objektidentitäten benötigen noch ihren vollständigen Archivbeleg.
Änderungen des aktiven Altkatalogs während der Lesung sind ausdrücklich erfasst.

Die unabhängige Abschlussprüfung bestätigte unveränderte Originale, Profile,
Hauptkatalog, PAUSE und acht false-Lösch-/Abnahmeflags. Beide neuen Archivdienste
und der Timer sind inactive/disabled, ohne Startzeit; INSTALLATION_READY fehlt.
Die installierte Runtime bleibt `55c067b4c8e5`/Codecommit `db847ed2`.
Temporäre Testeinheiten sind beendet und deren neue Testdateien entfernt.

Nachweise: `outputs/greenmind-staging-boxcoverage-20261028/Ergebnis.json`,
`Upload-Ergebnis.json`, `Snapshot-Restore-Ergebnis-v2.json`,
`SQL-Lebenszyklus-v2.json` und `Abschlusspruefung.json` im Projekt.
Serverseitige Restore-Belegdatei:
`/etc/greenmind/tiered-archive/gm-history-snapshot-c5qxp0uk-result.json`, SHA-256
`a920268d0ea7a88731b740da388aefc65a3af9dfde9e3ad9480e3bb3b331c471`.

Vor Gesamtabnahme verbleiben die vollständige Objektzuordnung, operative
SQL-Katalog-/Laufplanbindung, vollständige native SQL-Prozessabsturzfälle,
externe Alarmzustellung und nach eigener Startfreigabe der unveränderte echte
24-Stunden-Kopier-/Lesebetrieb. Produktionsanbindung, Ressourcen-/Durchsatz-
Nachweis und unabhängige zweite Schlüsselwiederherstellung bleiben gesondert.
