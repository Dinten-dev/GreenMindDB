# Staging-Review V2 — 26. Oktober 2026

Status: lokal vorbereitet, keine Installation, keine Freigabe angenommen.
Der bisherige Staging-Review wird durch diese Fassung ersetzt.

## 1. Umfang und Rechte

Das erste Profil heisst **isolierter Staging-Kopierlauf mit Rücklesung**.
Es schreibt tatsächlich neue Archivteile. Es ist kein Dry-Run.
Es verwendet weder produktive Quellzugänge noch produktive Archivzugänge.
Eine spätere Produktionsanbindung bleibt gesondert zu prüfen.

| Bereich | Berechtigung und konkreter Umfang |
|---|---|
| Gateway-PostgreSQL | SELECT, eigene Kopie `127.0.0.1:26432/gateway_staging` |
| Direct-PostgreSQL | SELECT, eigene Kopie `127.0.0.1:26433/direct_staging` |
| MinIO | GET/LIST/VERSIONS, eigene Kopie `127.0.0.1:26900` |
| Gateway-Bucket | `greenmind-raw`, ausschliesslich im Staging-MinIO |
| Direct-Bucket | `greenmind-direct-staging-review`, ausschliesslich dort |
| Kopierdienst lokal | Schreiben unter `/var/lib/greenmind/tiered-archive/staging/` |
| Archivleser lokal | Schreiben nur `staging/cache/`, `staging/control/`, eigener Laufzeitsocket |
| Box-Upload | Eigener RW-Unteraccount, Home `/greenmind-staging-20261026` |
| Tatsächliches Uploadziel | `/greenmind-staging-20261026/greenmind-raw/staging/` |
| Box-Leser | Anderer RO-Unteraccount, Home `/greenmind-staging-20261026/greenmind-raw` |
| Code und Profile | Root-eigen, kein Schreibrecht für Dienstkonten |
| Produktion und Löschung | Keine entsprechenden Quell-Schreibzugänge, alle Löschschalter aus |

Im Uploadprofil ist `greenmind-raw` der relative SFTP-Wurzelpfad.
Die Archivobjekte liegen darunter unter `staging/tiered/…`; Katalogteile
ebenfalls ausschliesslich unter dem Staging-Präfix. Der RW-Zugang darf
innerhalb seines Anbieter-Homes schreiben und technisch auch entfernen;
dieses Restrisiko betrifft ausschliesslich die isolierten Testdaten.
Eine unveränderliche Storage-Box-Berechtigung wird dadurch nicht behauptet.

Die Home-Zuordnung und RO/RW-Einstellung müssen vor dem ersten Start
im Hetzner-Konto nachgewiesen werden. Der Installer prüft Kontonamen und
Zielstruktur, kann diese Anbieterrechte aber nicht aus dem Namen beweisen.
Dasselbe gilt für tatsächliche SELECT- und S3-Leserechte. Fehlende oder
abweichende Nachweise verhindern die Betriebsfreigabe.

Die Dienstkonten `greenmind-archive` und `greenmind-archive-read` müssen
vor Installation als getrennte Systemkonten ohne Login bestehen. Der
Installer erzeugt keine Konten und ändert keine vorhandenen Gruppen.
Unter `/etc/greenmind/tiered-archive/` liegen `staging-upload.key` und
`staging-reader.key`, jeweils 0400 im Eigentum des betreffenden Dienstkontos.
Die angeheftete Hostschlüsseldatei `staging-known-hosts` ist root-eigen 0444.
Die tatsächliche Schlüsselbereitstellung erfolgt erst nach Installationsfreigabe.
Ein bereits bestehendes Konfigurationsverzeichnis oder Staging-Datenverzeichnis
führt vor Änderungen zum Abbruch; fremde Bestände werden nicht übernommen.

Hetzner dokumentiert Unteraccounts mit Verzeichnisbindung und RO-Option:
[Storage Box](https://docs.hetzner.com/storage/storage-box/general/),
[zusätzliche Benutzer](https://docs.hetzner.com/storage/storage-box/additional-users/).

## 2. Herkunft und Quelllast

Die erste Ausführung liest ausschliesslich bereits bereitgestellte lokale
Staging-Kopien. Keine neue Kopie aus Produktion entsteht durch Installation
oder Dienststart. Der Installer lehnt Produktionsadressen und das bisherige
Produktions-Telemetrieprofil ab. Eine zusätzliche IP-Sperre für
`188.245.247.156` ist nur eine weitere Schranke, keine vollständige Firewall.

Die Bereitstellung echter Datenkopien bleibt ein eigener Schritt. Vorher
sind Herkunft, Kopierzeitpunkt, Prüfsummen, Umfang und Kopierlast zu erfassen.
Die lokalen Datenbank-, MinIO- und Test-API-Dienste müssen separat begrenzt
und im gesamten Hostbudget berücksichtigt werden. Das neue 416-MiB-Budget
deckt diese Quellen ausdrücklich nicht ab.

Das Beispiel enthält noch keine SQL-Organisationen oder Zeitbereiche.
Vor SQL-Tests sind konkrete kleine Scopes aus dem geprüften Inventar
einzutragen. Ohne diese Scopes ist kein SQL-Staging nachgewiesen.

## 3. RAM, Disk und Nebenprozesse

| Ressource | Erste Staging-Grenze |
|---|---|
| Kopierdienst | `MemoryHigh=160M`, `MemoryMax=192M`, Swap 0, CPU 25 % |
| Sicherungsdienst | Eigene gleiche Grenze; manuell und separat gestartet |
| Archivleser | Eigene gleiche Grenze; DuckDB-Limit zusätzlich 64 MB |
| Gemeinsamer Staging-Slice | 416 MiB, Swap 0, CPU 50 %, 48 Tasks |
| WAV-Kopierlauf | Maximal zehn WAVs oder 5 MiB; zuerst erreichte Grenze |
| SQL-Seite | Maximal 500 Zeilen oder 1 MiB kanonische Daten |
| Dateicache | 512 MiB auf Disk, kein 512-MiB-RAM-Cache |

`pg_dump` bleibt als Kindprozess in der cgroup des Sicherungsdienstes.
Es hat **keine zusätzlichen 192 MiB**. Das Betriebssystem rechnet auch
zugeordneten Dateicache auf die Speichergruppe an. Streaming allein beweist
deshalb nicht, dass Python, Arrow und PostgreSQL-Client zusammen hineinpassen.
Quelle: [systemd-Ressourcenverwaltung](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html).

Kopieren und Sichern verwenden dieselbe exklusive Sperre `operation.lock`.
Sie laufen nicht gleichzeitig. Das erste Kopierprofil enthält keine
eingebetteten Sicherungen. Sicherungen haben keine Timeraktivierung.
Der gemeinsame Slice lässt den Leser plus einen Arbeitsschritt zu;
32 MiB Reserve berücksichtigen kurze Kontrollprozess-Überschneidungen.

Der frühere Vorschlag mit 64-MiB-WAV-Paketen und 8-MiB-SQL-Seiten gilt
nicht für den ersten Lauf. Ein vollständiges WAV-Revisionspaket, das
nicht ins Restbudget passt, bleibt unverändert für einen späteren Lauf.
Ein SQL-Einzelwert über 1 MiB blockiert den Schritt, statt Daten abzuschneiden.

Native Abnahme: cgroup v2, lesbares `memory.peak`, Höchstwert höchstens
160 MiB und null OOM-Kills bei repräsentativen Gateway-, Direct-, Reader-
und separaten Sicherungsläufen. Die lokalen Tests ersetzen diese Messung
nicht. Bei Überschreitung wird nicht automatisch das Limit erhöht.

## 4. Abbruch, Pause und Wiederaufnahme

| Auslöser | Reaktion |
|---|---|
| Hash-, Inhalts-, Quell- oder Berechtigungsfehler | Staging-Schritt schlägt fehl; dauerhafte Pause |
| Lokaler Healthcheck nicht 200 oder unerreichbar | Nächster Kontrollpunkt stoppt den Arbeitsschritt |
| Host-RAM verfügbar unter 1’024 MiB | Arbeitsschritt stoppt |
| Hostlast über 2 | Arbeitsschritt stoppt |
| Freier Speicher unter max. 2 GiB, 5 % | Arbeitsschritt stoppt |
| Gesamtlauf über 300 Sekunden | Python-Abbruch mit dauerhaftem Ergebnis |
| Prozess reagiert nicht | Nach 330 Sekunden TERM; weitere 30 Sekunden bis KILL |
| OOM, Dienstfehler, cgroup-Spitze über 160 MiB | `ExecStopPost` schreibt gemeinsame `control/PAUSE` |
| Bediener setzt `control/PAUSE` | Keine neuen Läufe; laufender Worker stoppt am Kontrollpunkt |

Ressourcen werden an Kontrollpunkten geprüft; erfolgreiche HTTP-Prüfungen
werden höchstens fünf Sekunden wiederverwendet. Netzwerk- und
Datenbankoperationen haben zusätzliche eigene Zeitlimits. Die 300 Sekunden
sind ein kooperatives Ziel; der harte Dienstabschluss liegt spätestens bei
360 Sekunden. Ein harter Abbruch kann unbestätigte Teiluploads hinterlassen.
Diese gelten nie als Archivnachweis und erlauben keine Löschung.

`tick` speichert Cursor und Nachweise dauerhaft. Ein neuer Lauf erfolgt
erst nach Ursachenprüfung und bewusster Entfernung der Pause; danach wird
der bestätigte Stand fortgesetzt. `Restart=no` verhindert automatische
Neustartschleifen. Der Timer wartet zehn Minuten nach Ende des letzten
Laufs; die erste Bootverzögerung beträgt fünf Minuten. Das Arbeitsbudget
beträgt fünf Minuten. Derselbe Dienst überlappt nicht; der Timer bleibt
bei Installation deaktiviert.

Zusätzliches operatives Kriterium: drei aufeinanderfolgende
API-Latenzmessungen über `max(1,2 × Ausgangswert, Ausgangswert + 100 ms)`
lösen eine Pause aus. **Dieser Vergleich ist noch nicht im Worker
automatisiert.** Bis eine vorhandene Überwachung nachweislich diese Pause
setzt, darf nur ein betreuter Lauf stattfinden. Ein unbeaufsichtigter
24-Stunden-Lauf ist damit noch nicht freigegeben.

## 5. Schlüssel und Wiederherstellung

| Element | Festlegung |
|---|---|
| Primärschlüssel | 32 zufällige Bytes, AES-GCM |
| Serverdatei | `/etc/greenmind/tiered-archive/staging-backup.key`, root:root, 0600 |
| Konfigurationsverzeichnis | root-eigen, 0710; Leserdienst nur Gruppendurchgang |
| Schlüsselzugriff | Root und Sicherungsdienst über `LoadCredential`; kein Kopier-/Leserzugriff |
| Zweite Schlüsselkopie | Verschlüsselter Tresor ausserhalb Server und Box, Verantwortung Traver |
| Recovery-Nachweis | `/etc/greenmind/tiered-archive/staging-key-recovery.json`, root:Lesergruppe, 0640 |
| Startbedingung | Passender Schlüsselfingerabdruck und erfolgreicher Roundtrip, maximal sieben Tage alt |

Der konkrete Tresoreintrag und dessen Wiederherstellungsweg sind noch
festzulegen. Es wurde weder ein echter Archivschlüssel erzeugt noch
ausserhalb des Servers hinterlegt. Ohne diese Einrichtung kein erster Start.

`prove-key-recovery.py` vergleicht eine separat wiederhergestellte Datei
mit dem Primärschlüssel und entschlüsselt damit eine frische Testnachricht.
Das Ergebnis bindet den Schlüsselfingerabdruck und die Tresorreferenz.
Die Dienstvorprüfung `verify-start.py` verlangt diesen geschützten Nachweis.
Ein Roundtrip allein beweist weder die externe Tresorberechtigung noch
eine unabhängige Wiederherstellung durch einen anderen Administrator.
Diese organisatorischen Nachweise bleiben Teil der Einrichtung.

Schlüssel gehören weder ins Paket, Git, Chat oder Log noch ausschliesslich
auf die Storage Box. Temporäre Recovery-Dateien sind nach erfolgreicher
Prüfung geschützt zu entfernen. Passwörter werden nur im Terminal eingegeben.

## 6. Socket und Zugriffsweg

Der einzige neue Listener ist ein lokaler Unix-Socket:
`/run/greenmind-tiered-archive-staging/history.sock`.
Verzeichnis 0700, Socket 0600, Eigentümer `greenmind-archive-read`.
Zusätzlich prüft der Broker dieselbe aufrufende UID.
Es gibt in dieser Phase keine SSH-, Agent-, TCP- oder DB-Weiterleitung.
Die Staging-Abfragen erfolgen lokal unter diesem Dienstkonto.

Eine spätere Verbindung produktiver APIs muss Ziel, Richtung, Identität
und Widerruf separat festlegen. `PermitOpen` beschränkt TCP-Ziele und
ist keine Pfadfreigabe für Unix-Sockets; dafür sind StreamLocal-Regeln
und lokale Dateiberechtigungen entscheidend. Bestehende Schlüssel und
Weiterleitungen werden hier nicht erweitert.
Quelle: [OpenSSH-Konfiguration](https://man.openbsd.org/sshd_config).

## 7. Release, Offlineinstallation und Timer

Das neue Paket enthält alle 60 hashgebundenen Wheels für CPython 3.12,
Linux x86_64, glibc mindestens 2.28. Beide Sperrdateien werden vollständig
ohne Index aufgelöst. Der Installer verwendet `--no-index`, `--isolated`,
`--no-cache-dir`, `--find-links` und `--require-hashes`.
Es gibt keine automatische Paketinstallation aus dem Netz.

Voraussetzungen sind ein vertrauenswürdiges root-eigenes CPython 3.12
mit venv/pip, systemd mit cgroup v2 und `LoadCredential`, OpenSSH-SFTP,
`flock` und ein zur Quelldatenbank passender PostgreSQL-Client.
Diese Betriebssystemwerkzeuge sind keine Wheels und müssen bereits
vorhanden sein. Der erste Installer baut und veröffentlicht kein Frontend.

Der Installationsordner lautet `/opt/greenmind/tiered-archive/releases/`
plus vollständiger Commit. Der konkrete 40-stellige Ordner steht im
begleitenden, nach dem Commit erstellten Prüfreport. `RUNTIME_SHA256`
ist keine Platzhalterdatei mehr: der Wert steht im `release.json`.
Nach Installation gehören Code und venv root; Verzeichnisse und Programme
sind 0555, sonstige Dateien 0444. Dienstbenutzer können sie nicht ändern.
Ein nativer Eigentümer-/Pfadcheck bleibt vor Ausführung erforderlich.

| Hash | Deckt ab |
|---|---|
| Runtime | Backend-Python, SQL und beide Python-Sperrdateien |
| Manifest | Exakte Bytes von `release.json`, einschliesslich Dateiliste und Commit |
| Dateien | Alle Paketdateien einschliesslich Wheels, Vorlagen und Deploymentwerkzeuge |
| Paket | Gesamte komprimierte TAR-Datei vor dem Entpacken |

Der Paketbau verlangt einen sauberen Commit. Sortierte Einträge, feste
Modi, Null-Zeitstempel und leerer gzip-Dateiname machen zwei Builds unter
derselben Python-/zlib-Version reproduzierbar. Dazu gehören die exakt
hashgebundenen Ziel-Wheels; ein Commit allein enthält deren Binärdaten nicht.

Der separate Bootstrap-Prüfer `verify-package.py` prüft den bekannten
Paket-Hash vor jeglichem Entpacken. Danach prüft `install-staging.py`
den separat festgehaltenen Manifest-Hash und sämtliche Dateiprüfsummen.
Die exakten Serverbefehle und Hashes stehen im begleitenden Prüfreport.
Ein Prüfer aus einem ungeprüft entpackten Paket ist kein Vertrauensanker.

Installation führt ausschliesslich `daemon-reload` und `disable` für die
neuen Einheiten aus. Kein `start`, `restart` oder `enable`. Neue Dienste
bleiben inaktiv; Timer disabled, Sicherungsvorlage nur manuell startbar.
Dies ist anschliessend mit `is-active` und `is-enabled` nachzuweisen.
Die bestehenden Produktionsdienste werden nicht umkonfiguriert.

## 8. Erfolgskriterien und Überwachung

Die erste Kopierphase ist erfolgreich, wenn Gateway und Direct aus den
festgelegten Kopien tatsächlich übertragen wurden, alle aktuellen
SFTP-Rücklesungen vollständig mit den Quellen übereinstimmen, Kataloge
rekonstruierbar sind und Ressourcen- sowie Zugriffsgrenzen eingehalten
werden. Erwartete unvollständige Kandidaten bleiben dokumentiert gesperrt.

Vor der vollständigen Staging-Abnahme sind zusätzlich nachzuweisen:

- Snapshot enthält die veröffentlichten unveränderten Archivteile.
- WAVs dekodieren vollständig; Sampleanzahl und Features stimmen.
- SQL-Zeilen und Metadaten werden verlustfrei wiederhergestellt.
- Separater Datenbank-Restore verwendet den wiederhergestellten Schlüssel.
- Neue, verspätete, doppelte und beschädigte Daten bleiben nachvollziehbar.
- Unterbrechung, volle Box, Netzwerkfehler und OOM sperren zuverlässig.
- Historische Abfragen, CSV und Mandantentrennung stimmen.
- Parallelzugriffe erfüllen Ressourcen- und Latenzgrenzen.
- Null unerwartete Fehler, Quellschreibvorgänge oder OOM-Kills.

Das erste Profil wählt und erstellt **keine Snapshots**. Ein späterer
Verify-Lauf benötigt separat geprüfte Snapshot-IDs, Sichtbarkeit und
Anbieterzugang. Ein erfolgreicher Kopierlauf ist keine Snapshot-Abnahme.

Die spätere Produktionsbeobachtung beginnt erst mit dem unveränderten
deployten Stand: mindestens 24 Stunden, mindestens 280 gültige Samples,
maximal zehn Minuten Messlücke, beide Empfangswege mit Fortschritt,
keine Fehler, Archivdurchsatz über laufendem Zuwachs und nachvollziehbar
abnehmender Rückstand. Gestartete Beobachtung genügt nicht.

Logs landen im systemd-Journal der neuen Einheiten. Private JSON- und
Prometheus-Textdateien liegen unter `staging/` beziehungsweise `staging/cache/`.
`self_peak_rss` ist nur Python-Prozess-RSS; `cgroup_memory_peak_bytes`
ist der getrennte Gruppenmesswert. Fehlende Gruppenwerte gelten nicht
als bestandene Speicherabnahme. Traver ist der benannte Staging-Bediener.
Es ist noch kein externer Alarmempfänger angeschlossen; unbeaufsichtigter
Betrieb wartet auf den getesteten Alarmweg.

## 9. Rücknahme und verbleibende Startbedingungen

Jeder unerwartete Fehler, Grenzwertverstoss oder unklare Ziel-/Quellidentität
löst Pause und Ursachenprüfung aus. Neue Staging-Einheiten werden bei
Bedarf gestoppt und deaktiviert; Profile, Paket, Logs und Archivbelege bleiben
erhalten. Produktionsreader laufen mit ihren bisherigen Profilen weiter.
Keine automatische Löschung von Testarchiven während der Rücknahme.

Vor Installation: konkreter Commit/Paketreview, legitime Administratoranmeldung
und gemeinsame Freigabe. Vor erstem Start zusätzlich: bereitgestellte
geprüfte Datenkopien, echte Quellenrechte, Anbieter-Home-Bindung, Schlüssel-
Recovery und Gesamtbudget des Hosts. Native Speicher-, Snapshot-, Restore-
und Laufzeitnachweise werden erst durch die betreuten Staging-Schritte
erbracht. Diese ausstehenden Nachweise werden nicht als erledigt dargestellt.

Die Löschautomatik bleibt ausgeschaltet. Für spätere Pilotlöschungen gilt
ebenfalls zehn WAVs **oder** 5 MiB, je nachdem, was zuerst erreicht wird.
SQL-Löschung benötigt eine eigene Abnahme und Freigabe.
