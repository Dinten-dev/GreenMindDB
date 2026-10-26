# Staging-Review V3 — 26. Oktober 2026

Diese Fassung ersetzt V2. Die Korrekturen sind lokal umgesetzt. Es wurde
kein Archivdienst installiert oder gestartet und keine Löschung aktiviert.
Auf dem Archivhost liefen ausschliesslich kurzlebige, begrenzte Diagnoseprozesse.

## Tatsächlicher Host und Installationsgrenzen

Schreibgeschützt geprüft am 26. Oktober, 20:58 UTC:

| Eigenschaft | Gemessener Stand |
|---|---|
| Archivhost | `2.31.22.33`, Linux x86_64 |
| RAM gesamt | 4’000’034’816 Bytes, 3’814,7 MiB |
| RAM verfügbar | 1’618’026’496 Bytes, 1’543,1 MiB; Momentaufnahme |
| Prozessoren | 2 logische CPUs |
| 1-Minuten-Load | 0,333 |
| System-Python | `/usr/bin/python3` → Python 3.14.4 |
| Vorgesehenes Runtime-Python | `/usr/bin/python3.12` fehlt |
| systemd | 259 |
| Dateisystem für `/` und `/var/lib` | `/dev/sda1`, 39’956’590’592 Bytes |
| Verfügbarer Plattenplatz | 2’210’557’952 Bytes, rund 2,06 GiB |
| Dienstkonten | Leser vorhanden; `greenmind-archive` noch nicht vorhanden |
| Administratorzugang | Reguläre Anmeldung erforderlich; kein allgemeines passwortloses sudo |

Die bestehende Plattenreserve beträgt `max(2 GiB; 5 % der Partition)`.
Hier sind das 2’147’483’648 Bytes. Der Spielraum liegt nur bei rund
63 MB. Schon das vollständige Offline-Paket passt nicht zusätzlich in
diesen Spielraum. Deshalb keine Paketübertragung oder Installation unter
Unterschreitung der bestehenden Reserve.

`disk_budget()` berücksichtigt vor Installationsänderungen die Releasekopie,
die unkomprimierten Wheel-Inhalte, 64 MiB Einrichtungsreserve und die
weiterhin freie Partitionsreserve. Upload und Entpacken benötigen zusätzlich
Platz; vorhandene Daten werden dafür nicht ungeprüft gelöscht.

416 MiB Staging-Slice plus 1’024 MiB RAM-Reserve lassen beim gemessenen
Stand nur rund 103 MiB für weitere gleichzeitig laufende Testquellen.
Zwei PostgreSQL-Instanzen, MinIO und Test-APIs sind darin nicht budgetiert.
Ihre konkreten Limits und das Gesamtbudget bleiben offen. Die vorhandenen
4 GB gelten ausdrücklich nicht als bereits abgenommenes Staging-Hostbudget.

## Herkunft, Schreibrechte und getrennte Namen

Das erste Profil heisst **isolierter Staging-Kopierlauf mit Rücklesung**.
Es führt echte Uploads und aktuelle Rücklesungen aus; es ist kein Dry-Run.
Es liest ausschliesslich vorbereitete, geprüfte lokale Datenkopien.
Die Bereitstellung dieser Kopien bleibt ein eigener, begrenzter Schritt.

| Bereich | Erlaubter Umfang |
|---|---|
| Gateway-SQL | SELECT auf `127.0.0.1:26432/gateway_staging` |
| Direct-SQL | SELECT auf `127.0.0.1:26433/direct_staging` |
| Staging-MinIO | GET/LIST/VERSIONS auf `127.0.0.1:26900` |
| Gateway-Bucket | `greenmind-gateway-staging-20261026` |
| Direct-Bucket | `greenmind-direct-staging-review` |
| RW-Box-Unteraccount | Anbieter-Home `/greenmind-staging-20261026` |
| Relatives Uploadverzeichnis | `greenmind-staging`, ausschliesslich Unterpräfix `staging/` |
| Physisches Uploadziel | `/greenmind-staging-20261026/greenmind-staging/staging/` |
| Separater RO-Unteraccount | Home `/greenmind-staging-20261026/greenmind-staging` |
| Lokaler Kopierzustand | `/var/lib/greenmind/tiered-archive/staging/` |
| Leser-Schreibbereiche | `staging/cache/`, `staging/control/`, eigener Laufzeitsocket |

Die alternative Absicherung durch eindeutig andere Namen wird verwendet:
kein Produktions-Bucketname für Gateway-Kopien und kein gemeinsames
Box-Wurzelverzeichnis. Der Worker übernimmt den Gateway-Bucket bis in die
konkrete WAV-Quellidentität. Produktionsprofile lehnen diesen Bucket ab.
Beim Katalog-Restore müssen Zielumgebung, Wurzelreferenz, Katalogteile und
enthaltene Quellnachweise übereinstimmen. Staging-Kataloge können dadurch
nicht versehentlich als Produktionskatalog importiert werden. Bestehende
Katalogformate bleiben mit Prüfung ihrer enthaltenen Umgebung lesbar.

Der Installer prüft die Profilstruktur. Tatsächliche SELECT-/S3-Leserechte,
Anbieter-Home-Bindung, RO/RW-Einstellung und Herkunft der Kopien sind vor
erstem Start separat nachzuweisen. Die RW-Berechtigung kann innerhalb
ihres isolierten Test-Homes technisch auch löschen; sie ist kein WORM-Schutz.
Produktive Quellzugänge und historische Sonderfreigaben werden nicht übernommen.

## Speicherentscheidungen und laufende Messung

| Einstellung | Verhalten |
|---|---|
| Kopieren, Sicherung, Leser | Je `MemoryHigh=160M`, `MemoryMax=192M`, Swap 0 |
| CPU | Je 25 %, gemeinsamer Staging-Slice 50 % |
| Gemeinsamer Slice | 416 MiB; Testquellen haben eigene, noch offene Budgets |
| `memory.peak` | Beobachtung, keine Pausenschwelle bei 160 MiB |
| `memory.events: high` | Warnung wegen Drosselung, allein keine Pause |
| `memory.events: max`, `oom`, `oom_kill` | Wert über null setzt Pause und stoppt Arbeit |
| `memory.stat: anon` | Über 128 MiB setzt Pause und stoppt Arbeit |
| Fehlende Pflichtmesswerte | Keine bestandene Messung; Betrieb bleibt gesperrt |

Der neue Sampler liest alle 0,5 Sekunden, einschliesslich Beginn und Ende.
Er erfasst den höchsten beobachteten Anonymspeicher, Ereignisse und
Messfehler unter einer systemd-Invocation-ID. Kurze Spitzen zwischen
Abtastungen können beim Anonymspeicher unbemerkt bleiben; das ist kein
exakter Kernel-Höchstwert. Kernel-Max-/OOM-Ereignisse bleiben unabhängig
davon Stoppsignale. Der Kernel-Gesamthöchstwert wird zusätzlich aufbewahrt.

`ExecStopPost` liest verbleibende Gruppenwerte und den passenden laufenden
Messnachweis. Ein alter Nachweis einer anderen Invocation genügt nicht.
Unvollständige oder fehlende Messung blockiert die Abnahme. Gemessener
Python-Prozess-RSS wird ausdrücklich nicht als cgroup-Speicher ausgewiesen.

Der native Diagnoseprozess mit 64-MiB-Grenze und CPU-Limit konnte nach
Ende des Hauptprozesses in `ExecStopPost` `memory.events`, `memory.stat`
und `memory.peak` vollständig lesen. Alle Max-/OOM-Zähler blieben null.
Die konkrete Ausführung der Projekt-Helfer auf dem Server ist noch nicht
erfolgt: Ihre Codeübertragung wurde von der automatischen Freigabeprüfung
wegen fehlender ausdrücklicher Zustimmung blockiert. Der allgemeine
Diagnoseerfolg ersetzt weder diesen Helfertest noch einen echten Archivlauf.

`pg_dump` bleibt innerhalb der Sicherungs-cgroup und erhält kein zusätzliches
Budget. Kopieren und Sichern verwenden dieselbe exklusive Sperre und
laufen nicht gleichzeitig. Sicherungen sind separate manuelle Dienste;
im ersten Kopierprofil gibt es keine eingebetteten Backupaufträge.

Der Cache ist eine auf 512 MiB begrenzte Dateiablage auf Disk. Dateicache
wird trotzdem teilweise auf die cgroup angerechnet. Die erste Kopierphase
endet bei zehn WAVs oder 5 MiB, je nachdem, was zuerst erreicht wird.
Ein vollständiges Revisionspaket, das nicht hineinpasst, bleibt für später.
SQL-Seiten enden bei 500 Zeilen oder 1 MiB; übergrosse Einzelzeilen werden
blockiert und niemals abgeschnitten.

Quelle für Ereignisse und Speicherarten:
[Linux-cgroup-v2-Dokumentation](https://docs.kernel.org/admin-guide/cgroup-v2.html).

## Abbruch und Wiederaufnahme

| Kriterium | Präzise Grenze |
|---|---|
| Inhalt, Quelle, Berechtigungen | Unerwarteter Fehler pausiert den Staging-Schritt |
| Healthchecks | Lokale Staging-Endpunkte müssen HTTP 200 liefern |
| Host-RAM | Verfügbarer RAM mindestens 1’024 MiB |
| Hostlast | 1-Minuten-Load höchstens `min(2; 0.5 × logische CPUs)` |
| Aktueller Zweikernhost | Somit Load höchstens 1,0 |
| Platte | Frei mindestens `max(2 GiB; 5 % der jeweiligen Partition)` |
| Überwachte Worker-Partition | Partition hinter `RAW_ARCHIVE_SOURCE_MOUNTS`, aktuell `/var/lib/…/staging` auf `/dev/sda1` |
| Datenkopien auf anderen Partitionen | Deren tatsächliche Mountpfade vor Start zusätzlich aufnehmen |
| Gesamtlauf | Kooperativer Abbruch nach 300 Sekunden |
| Nicht reagierender Prozess | Nach 330 Sekunden TERM, spätestens weitere 30 Sekunden bis KILL |
| Speicherdruck | Wie oben: Max-/OOM-Ereignisse oder Anonymspeichergrenze |

Latenzgrenze: drei aufeinanderfolgende API-Messungen über
`max(1.2 × Ausgangswert; Ausgangswert + 100 ms)` bedeuten Pause.
Der Vergleich ist noch nicht automatisiert. Unbeaufsichtigter Betrieb
wartet auf einen nachweislich angeschlossenen und getesteten Alarmweg.

Eine dauerhafte `control/PAUSE` verhindert Folgeläufe. Bereits aktive
Worker stoppen am Kontrollpunkt; Leser verweigern weitere Abfragen.
`Restart=no` verhindert Neustartschleifen. Wiederaufnahme erfolgt nach
Ursachenprüfung und bewusster Entfernung der Pause über gespeicherte
Cursor und Nachweise. Unbestätigte Teiluploads erlauben keine Löschung.

Der Timer bleibt disabled. Er wartet zehn Minuten nach Ende des letzten
Laufs; Bootverzögerung und Arbeitsbudget betragen jeweils fünf Minuten.
Es gibt keine parallele zweite Instanz desselben Kopierdienstes.

## Schlüssel, Verzeichnisrechte und Recovery

`/etc/greenmind/tiered-archive/` gehört root:root mit 0700. Profile,
Upload-/Leserschlüssel, AES-Backupschlüssel und Recovery-Nachweis sind
root-eigen 0600. Dienstbenutzer benötigen keinen Verzeichnisdurchgang.

`LoadCredential` stellt jedem Dienst ausschliesslich seinen SFTP-Schlüssel
und die angeheftete Hostschlüsseldatei bereit. Nur der Sicherungsdienst
erhält zusätzlich den AES-Schlüssel. Der Installer entfernt konkurrierende
Schlüsselpfade aus den EnvironmentFiles, damit diese die Credentialpfade
nicht überschreiben. Er entfernt ebenso eine mögliche Überschreibung der
fest eingeschalteten cgroup-Messung.

`ExecStartPre=+` führt ausschliesslich den unveränderlichen, hashgeprüften
Schlüssel-Nachweisprüfer als root aus. Dieser liest den root-privaten
Recovery-Nachweis; Kopier- und Leserdienst erhalten ihn nicht. Gruppenrechte
zwischen den Diensten dienen nur dem Katalog-Leseübergang und der Pause,
nicht dem Zugriff auf `/etc` oder die Schlüssel.

Der Backupschlüssel besteht aus 32 zufälligen Bytes. Vor erstem Start muss
er in Travers externem verschlüsselten Tresor hinterlegt und separat
zurückgeholt werden. Der konkrete Tresoreintrag bleibt festzulegen.
Ein Roundtrip vergleicht den zurückgeholten Schlüssel und entschlüsselt
eine neue Testnachricht. Nachweis und gepinnter Fingerabdruck müssen passen;
der Nachweis darf höchstens sieben Tage alt sein. Noch wurde kein echter
Schlüssel erzeugt oder im Tresor hinterlegt.

Für Produktion ist zusätzlich eine zweite benannte Person erforderlich,
die den Schlüssel unabhängig aus der Hinterlegung wiederherstellen und
einen Archiv-Restore durchführen kann. Traver allein genügt für diese
Produktionsabnahme nicht. Schlüssel gehören weder ins Git/Paket/Chat/Log
noch ausschliesslich auf Server oder Box. Passwörter nur im Terminal.

## Socket, Paket und Vertrauensanker

Der neue Listener ist ausschliesslich der lokale Unix-Socket
`/run/greenmind-tiered-archive-staging/history.sock`: Verzeichnis 0700,
Socket 0600, Eigentümer `greenmind-archive-read`, zusätzliche UID-Prüfung.
Keine neue SSH-, Agent-, TCP- oder DB-Weiterleitung. Eine spätere
Produktionsweiterleitung benötigt einen eigenen Review.

Der Bootstrap-Prüfer benötigt CPython mindestens 3.10 und ist unabhängig
vom späteren CPython-3.12-venv. Der gemessene Systeminterpreter ist 3.14.4.
Der native Test dieses konkreten Prüfers bleibt bis zur Codeübertragungs-
freigabe offen. Lokale Tests prüfen sichere und bösartige TAR-Header.

Vor Entpacken wird das gesamte komprimierte Paket gegen den getrennt
festgehaltenen Hash geprüft. Der Prüfer lässt ausschliesslich reguläre
Dateien zu: keine absoluten Pfade, Traversals, Symlinks, Hardlinks,
Gerätedateien, FIFOs, doppelte Namen oder Datei-/Verzeichniskonflikte.
Header werden vor jeder Ausgabe vollständig begrenzt geprüft. Falls
vorhanden, läuft zusätzlich explizit `tarfile.data_filter`; die eigene
strengere Prüfung gilt unabhängig von Python-Defaults. Ausgabe nur in
ein neues privates Verzeichnis, Dateien ausschliesslich neu angelegt.
Quelle: [Python-TAR-Dokumentation](https://docs.python.org/3/library/tarfile.html#extraction-filters).

Das Paket enthält 60 vollständig hashgebundene Linux-x86_64-Wheels für
CPython 3.12. Pip arbeitet ausschliesslich mit `--no-index`, `--isolated`,
`--no-cache-dir`, `--find-links` und `--require-hashes`. OS-Werkzeuge und
CPython 3.12 müssen vorher vertrauenswürdig vorhanden sein. Keine stille
Netzinstallation und kein Frontenddeployment durch diesen Installer.

Runtime-Hash: Backend-Python/SQL und beide Python-Sperrdateien.
Dateihashes: jede Paketdatei einschliesslich Wheels und Deploymentwerkzeugen.
Manifest-Hash: exakte `release.json`-Bytes samt Commit und Dateiliste.
Paket-Hash: exakte komprimierte TAR-Bytes. Gleicher sauberer Commit,
Ziel-Wheels und Python-/zlib-Buildumgebung ergeben reproduzierbare Pakete.
Konkrete Werte und der vollständige Commit-Installationspfad stehen im
begleitenden Releaseprüfreport.

Der Review mit den erwarteten Hashes wird über diesen Codex-Chat bestätigt.
Das Paket wird später über einen anderen Transport übertragen. Ein
Review/Hashzettel, der ausschliesslich zusammen mit dem Paket über SFTP
ankommt, ist kein unabhängiger Vertrauensanker.

## Testausnahmen, Abnahme und Einführung

Der Releaseprüfreport enthält jede abgewählte und übersprungene Test-ID
mit Grund sowie separat ausgeführte Tests. Sicherheitskritische Lösch-,
Verify-, Restore-, Ressourcen- und Bootstrapmodule werden einzeln mit
Zeilenabdeckung und fehlenden Zeilen ausgewiesen. Gesamtcoverage ist kein
Ersatz für diese Pfade oder für native Betriebsnachweise.

Erfolg der ersten Kopierphase bedeutet: beide Empfangswege tatsächlich
aus Kopien übertragen, sämtliche aktuellen SFTP-Rücklesungen korrekt,
Katalog rekonstruierbar, kein unerwarteter Fehler, keine Quellschreibvorgänge,
kein Max-/OOM-Ereignis und alle gemessenen Anonymwerte höchstens 128 MiB.
Der erste Lauf erstellt und wählt keine Snapshots. Eine Kopierabnahme
ist keine Snapshot- oder Löschabnahme.

Vollständige Staging-Abnahme benötigt zusätzlich Snapshot-Rücklesung,
WAV-Dekodierung und Samplezahlen, verlustfreien SQL-/Feature-/Metadaten-
Restore, Datenbanksicherungs-Restore mit zurückgeholtem Schlüssel, späte/
doppelte/beschädigte Daten, Unterbrechungen, volle Box, Netzwerkfehler,
abgelaufene Nachweise, parallele Abfragen, CSV und Mandantentrennung.

Erst danach: Produktion ohne Löschung, mindestens 24 Stunden unverändert
beobachtet, mindestens 280 gültige Samples, höchstens zehn Minuten Lücke,
beide Empfangswege mit Fortschritt und Archivdurchsatz über Datenzuwachs.
Pilotlöschung und geprüfter Restore benötigen danach eine eigene Freigabe.
Zuerst zehn WAVs oder 5 MiB; SQL-Löschung separat.

Logs: Journal der neuen Dienste sowie private JSON-/Prometheus-Dateien
unter Staging beziehungsweise dessen Cache. Bediener: Traver. Ein externer
Alarmweg ist noch nicht angeschlossen. Bei Fehlern Pause, neue Einheiten
stoppen und deaktivieren, Produktionsleser unverändert weiterbetreiben,
Belege erhalten. Ein Software-Rollback ersetzt keinen Daten-Restore.

Installation bleibt ohne Dienststart und ohne aktivierte Timer. Ein
laufender Installer benötigt zusätzlich freien Plattenplatz, CPython 3.12,
Dienstkonten, legitime Administratoranmeldung und konkret geprüfte Profile.
Fehlende Voraussetzungen werden nicht durch schwächere Grenzen ersetzt.
