# Production: WAV-Sicherung ohne Löschung

**Nachtrag 26.09.2026:** Die Nachtreserve wurde nach dem geprüften 512-MiB-Test
von 1.024 auf **512 MiB verfügbaren Host-RAM** reduziert. Die tatsächliche
Prozessgrenze bleibt 128 MiB, eigener Swap bleibt gesperrt. CPU-, Last-,
Empfangsdienst- und Löschsperren bleiben unverändert. Ein zusätzlicher begrenzter
Test am 26.09. wurde wegen hoher Hostlast vor dem ersten Transfer angehalten;
die vier bestehenden Archivbelege blieben erhalten. Der nächste Timer ist für
27.09.2026 um 00:00 Schweizer Zeit vorgesehen. Ein erfolgreicher vollständiger
Nachtlauf ist weiterhin nicht nachgewiesen. Die ursprünglichen Messungen und
Konfigurationswerte vom 25.09. stehen unten als historische Abnahme.

Stand: 25.09.2026. Auf ausdrücklichen Auftrag wurde **ausschließlich die Kopierfunktion**
auf Production installiert, getestet und aktiviert. Keine Freigabe zur Entfernung
lokaler Originale. Kein vollständiger Altbestand- oder Nachtlauf abgeschlossen.

## Betrieb und Löschsperre

- Eigener unprivilegierter Benutzer und Dienst `greenmind-raw-copy.service`.
- `greenmind-raw-copy.timer`: täglich 00:00 `Europe/Zurich`, einschließlich Zeitumstellung.
- Nächster Termin bei Abnahme: 26.09.2026 00:00 Schweizer Zeit / 25.09.2026 22:00 UTC.
- Code: `/opt/greenmind/raw-copy/20260925`; Konfiguration: `/etc/greenmind/raw-copy`.
- Journal und Prüfbelege: `/var/lib/greenmind-raw-copy`.
- Ziel: `u676312.your-storagebox.de:23`, `greenmind-raw/production/AUFNAHMETAG/SENSOR/`.
- Vorhandener Sync-Schlüssel und gespeicherte SSH-Hostschlüssel; kein ungeprüfter neuer Hostschlüssel.

Der Production-Einstieg `deploy/raw-archive/copy-only.py` erzwingt deaktivierte
Lösch-, Archivlese- und Retention-Freigaben unabhängig von Umgebungswerten. Die
Konfiguration setzt dieselben Freigaben zusätzlich auf `false`. Der eigene
MinIO-Benutzer darf nur Originalobjekte lesen; eine explizite Deny-Policy verbietet
unter anderem `DeleteObject`, `DeleteObjectVersion`, `PutObject` und Lifecycle-Änderungen.
Keine Administrator-Zugangsdaten im Kopierdienst. Das PostgreSQL-Konto besitzt
SELECT, aber kein INSERT, UPDATE oder DELETE auf den verwendeten Tabellen.

**Keine RAW-WAV wurde gelöscht.** Auch der frühere große WAV-Export bleibt unverändert.
Eine spätere Löschfreigabe erfordert eine gesonderte Änderung und Abnahme;
das Umstellen eines Löschflags in diesem Dienst reicht nicht.

Die vorhandenen Empfangs-, Frontend-, API-, PostgreSQL- und MinIO-Container wurden
nicht ersetzt oder neu gestartet. Die vorbereiteten Archiv-Lesewege wurden nicht
in laufende APIs ausgerollt: Alle Originale bleiben für bestehende Downloads lokal.
In PostgreSQL wurden ausschließlich das SELECT-Konto und zwei Leseindizes ergänzt,
mit `CONCURRENTLY` und kurzem Lock-Timeout. Keine Messdatenänderung.

## Reale Abnahme

**Vier WAVs: eine Gateway-Datei und drei Direct-Dateien, insgesamt 1.389.790 Bytes.**
Nach jedem Upload wurde die endgültige Archivdatei vollständig zurückgelesen;
Länge und SHA-256 stimmten mit dem Katalog überein. Danach wurden Archiv und
weiterhin vorhandenes MinIO-Original unabhängig erneut vollständig gelesen und
verglichen. Diese Prüfung wurde wiederholt und bestand auch nach dem Kopierlauf.
Alle vier Originale haben dieselbe S3-Identität und denselben Inhalt. Die WAVs
lassen sich öffnen und enthalten 380-Hz-Samples. Ein vorhandener Prüfbeleg wurde
beim Wiederholungslauf wiederverwendet.

Der begrenzte Wiederholungslauf hatte null Dateifehler, drei neue verifizierte
Dateien und einen wiederverwendeten Beleg. Er meldete korrekt `incomplete`, weil
absichtlich nur vier Katalogreferenzen gescannt wurden und der Gesamtbestand
offen bleibt. Dies ist kein Nachweis einer vollständigen Serversicherung.

Während 15:42–15:45 UTC, einschließlich der Kopiertests:

| Empfangspfad | Nachweis |
|---|---|
| Peter Büro | 326 gespeicherte Messzeilen |
| Reihe 28 | 1.440 gespeicherte Messzeilen |
| Ruedis Gewächshaus | 1.620 gespeicherte Messzeilen |
| Direct-to-Cloud | 180 Uploadantworten mit HTTP 201 |

Gateway- und Direct-Gesundheitsprüfungen blieben erfolgreich; während des
erfolgreichen Kopierlaufs ungefähr 4 bzw. 7 ms Antwortzeit. Das öffentliche
Dashboard lieferte HTTP 200. Startzeiten und Neustartzähler der acht überprüften
Bestandscontainer blieben identisch. Der Kopierlauf verbrauchte maximal **90 MiB RAM**,
**0 Byte eigenen Swap** und ungefähr **3,3 CPU-Sekunden**.

Die Abschlussprüfung um 15:52 UTC zeigte weitere WAV-Eingänge von 20 Gateway-Sensoren
bis 15:50:30 UTC. Der Direct-Sequenzzähler stieg von 24.160 auf 25.219. Alle 119
installierten Python-Quelldateien stimmen mit dem geprüften lokalen Stand überein.

Die Stichprobe zeigt keine beobachtete Unterbrechung. Sie schließt zukünftige
Ausfälle oder Beeinträchtigungen unter anderer Last nicht aus.

## Ressourcen und verbleibende Kapazitätsgrenze

| Automatischer Nachtlauf | Einstellung |
|---|---:|
| Arbeitsspeicher maximal / Softlimit | 128 / 96 MiB |
| Eigener Swap | 0 |
| CPU | höchstens 25 % eines Kerns |
| Priorität | Nice 19, I/O idle |
| Erforderlicher verfügbarer Host-RAM am 25.09. | mindestens 1.024 MiB |
| Maximaler Host-Load, 1 Minute | 2 |
| Originalvolumen / Dateien | höchstens 1 GiB / 5.000 |
| Arbeitsbudget / harte Dienstgrenze | 1 Stunde / 75 Minuten |
| SFTP / S3-Lesen | jeweils höchstens etwa 1 MiB/s |
| DB-Abfrage / Lock-Timeout | 3 s / 500 ms |

**Bei der Abnahme waren nur ungefähr 690–760 MiB Host-RAM verfügbar; der Swap war
nahezu voll.** Der reguläre Schutz verweigerte deshalb den Start. Für den begrenzten,
beobachteten Test galt vorübergehend eine 512-MiB-Reserve bei höchstens 128 MiB
Prozessspeicher und 128 KiB/s SFTP. Diese Testausnahme wurde danach entfernt.
Die Lastgrenze blieb unverändert und stoppte einen angefangenen Lauf korrekt.
Bei der Abschlussprüfung waren 801 MiB verfügbar, weiterhin unter der Nachtgrenze.

Die am 25.09. aktive 1-GiB-Reserve führte zum Schutzstopp des ersten Nachtlaufs.
Seit dem 26.09. gilt die geprüfte niedrigere 512-MiB-Reserve; bei hoher Last
oder zu wenig RAM **pausiert der Dienst weiterhin**. Eine vollständige tägliche
Sicherung ist noch nicht zugesichert. Vor einer solchen Zusage müssen freie
Serverkapazität, Tageszuwachs und Durchsatz geklärt werden. Keine alten Dienste
wurden dafür eigenmächtig gestoppt. Die vollständige Nachtlast ist noch nicht getestet.

## Kontrolle und Rücknahme

Berichte stehen im systemd-Journal von `greenmind-raw-copy.service`. `blocked`
bedeutet Schutzpause, `incomplete` verbleibende Arbeit. Beides bleibt sichtbar
und wird nicht als vollständige Sicherung ausgegeben. Rückstände werden fortgesetzt.

Zum Stoppen nur `greenmind-raw-copy.timer` und gegebenenfalls dessen Service
anhalten. Ein `PAUSE`-Marker im Zustandsverzeichnis stoppt am nächsten Prüfpunkt.
Originale, Archivdateien und Journal erhalten. Keine Rückmigration nötig, weil
nichts lokal entfernt wurde. Bestehende Empfangsdienste bleiben unangetastet.

Aktuelle lokale Tests: **64 bestanden, 3 PostgreSQL-Tests übersprungen** (keine lokale
Testdatenbank gestartet). Ruff und `git diff --check` bestanden. Die umfassende
vorherige Abnahme mit isoliertem PostgreSQL bleibt im ursprünglichen Prüfbericht.
Reale S3-Lesezugriffe, SFTP, Rücklesen und PostgreSQL-Leserechte wurden jetzt
zusätzlich auf Production geprüft. Keine Löschtests ausgeführt.

Evidenz und verwendete Installations-/Prüfskripte:
`implementation/validation/raw-copy-production-20260925/` im Integrationsverzeichnis.
