# Freigabeumfang: Dashboard-Verdichtung, 15. September 2026

Beauftragt: gestaffelte Dashboard-Historie und Originalsignalansicht auf dem Server, laufender Empfang bleibt erhalten.

- 24 Stunden bleiben als Sekundenmesswerte erhalten. Im Dashboard werden längere Ansichten passend zusammengefasst.
- Tage 2–7: Minutenfenster; danach Zehnminutenfenster. Bestehende Wochen-Chunks können die physische Aufbewahrung der Sekundenwerte um bis zu eine Woche verlängern.
- Mittelwerte und exakte Identitätslisten verhindern Doppelzählung bei verspäteten und wiederholten alten Daten.
- Ausschläge, RMS, Streuung und Abdeckung stammen aus überprüften WAV-Samples; zeitlich unzuordenbare WAVs werden nicht künstlich aufgefüllt.
- Separate Lese-API und Hintergrundaufbereitung, CPU-/Speicherlimits, kurze Sperrwartezeiten. Die alten Empfangsdienste werden weder ersetzt noch neu gestartet.
- Vor jeder historischen Entfernung: vollständiges komprimiertes Archiv auf dem separaten Root-Dateisystem, Prüfsumme, erneutes Einlesen, unveränderter Quellstand und abgeschlossene WAV-Auswertung. Aktuelle Chunks bleiben erhalten.
- Sequenz-/Qualitätsmetadaten, die alte Auswerter weiterhin benötigen, sperren die Entfernung des betreffenden Chunks. WAVs, Anmeldungen, Geräte, Organisationen und Empfangsbelege bleiben erhalten.
- CSV bleibt authentifiziert, begrenzt und enthält Zuordnungen sowie die tatsächliche Auflösung.
- Bereitstellung zuerst als separate Staging-Dienste. Danach separate Production-Dienste; kein vollständiger Main-Release. Production-Frontend basiert auf Main und übernimmt ausschliesslich die Diagrammerweiterung; andere Develop-Änderungen bleiben auf Staging.
- Nginx schaltet ausschliesslich Frontend und Dashboard-Lesewege um. Gateway-, Direct-, Login- und WebSocket-Wege bleiben bei ihren bisherigen Diensten.

## Speicher und manuelle Systemrechte

Aktuell ungefähr 100 MiB verfügbarer RAM bei 4 GiB Gesamtspeicher, kein Swap. Neue Dienste werden deshalb noch nicht gestartet. `prepare-memory.sh` ergänzt 4 GiB Swap auf der Root-Partition (61 GiB frei), sichert fstab vor Änderung und startet keine Anwendungsdienste neu. Das erfordert sudo; der SSH-Benutzer traver besitzt dafür keine passwortlosen Rechte.

## Rückweg

Vor Entfernung historischer Chunks: neue Worker stoppen, vorherige Nginx-Konfiguration zurückspielen und Nginx sanft neu laden. Nach Entfernung: die neue Lese-API weiterbetreiben, bis die geprüften Originalarchive zurückgespielt sind. Nur das Frontend kann unmittelbar auf die vorherige Version zurückgeschaltet werden. Archive werden nicht automatisch gelöscht.

## Lokale Prüfung

17 Tests gegen eine separate echte TimescaleDB bestanden, einschliesslich komprimierter Tabellen, gleichzeitiger aktueller Einlieferung, Nachlieferung, Wiederholung, Alterungsgrenze, verweigerter Sperre, Mandantentrennung, beschädigtem Backup und vollständiger idempotenter Rücksicherung. Frontend-Typprüfung, Lint, 22 Tests und Produktionsbuild bestanden; alle drei finalen Images für linux/amd64 gebaut. Weitere 230 Backendprüfungen bestanden (drei davon auch in den 17 gezielten Tests enthalten). Die Diagramme wurden zusätzlich auf Desktop und Mobilbreite mit einer echten Production-Stichprobe vom 15. September geprüft. Das API-Image startet lokal innerhalb des 192-MiB-Limits und verweigert nicht authentifizierte Zugriffe mit 401.


## Bereitstellungsstatus

Staging: Paket unter `/home/traver/greenmind-visual-staging`, Compose-Konfiguration und Nginx-Entwurf geprüft. Der Nginx-Test nutzt ausschliesslich temporäre Loopback-Prüfports und ein Testzertifikat. Noch keine neuen Servercontainer gestartet; noch keine Visualisierungstabellen auf dem Server erstellt, keine Daten entfernt und kein öffentlicher Proxy umgeschaltet.

Production: getrenntes Image auf Main-Basis lokal fertig. Die automatische Freigabeprüfung hat die Production-Übertragung abgelehnt, weil sie die Staging-Prüfung und ausdrückliche Production-Freigabe verlangt. Kein Production-Paket übertragen.

Ausstehend: manueller Swap-Schritt wegen fehlender sudo-Rechte; danach vollständiger Staging-Lauftest, sanfte Proxy-Aktivierung mit sudo und externe Prüfung. Erst danach darf der Pruning-Marker gesetzt werden. Production benötigt zusätzlich die ausdrückliche Freigabe nach dem Prüfblock.

## Ausführung nach Speicherfreigabe

1. Nur das Staging-Imagearchiv laden, dann `start-standby.sh` ausführen. Das Skript verweigert den Start ohne mindestens 2 GiB freie Speicherreserve und stoppt keine vorhandenen Dienste.
2. `validate-standby.sh` prüft reale Daten, Authentifizierung, Original-WAV-Ausschnitt, CSV und unveränderte Startzeitpunkte der alten Dienste. Erst dieser Erfolg erzeugt die Aktivierungsfreigabe.
3. Mit sudo `python3 activate-proxy.py` ausführen. Alte Proxy-Konfiguration wird gesichert; Änderungen am Ausgangsstand führen zum Abbruch. Fehler beim Nginx-Test führen zur Rücksicherung.
4. Öffentliche, authentifizierte Endpunkte und Frontend prüfen. Dann `state/enable-pruning` anlegen. Ohne Marker werden historische Quelldaten auch bei laufendem Worker nicht entfernt.
5. Worker-Status und Archivwachstum beobachten. Nachbearbeitung ist gedrosselt und kann lange dauern; `paused_for_host_load` bedeutet, dass laufender Betrieb Vorrang erhält. Noch keine Aussage über insgesamt freigegebenen Speicher treffen.
