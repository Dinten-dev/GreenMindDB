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

Der Server besitzt drei CPUs und 4 GiB RAM. Zu Beginn waren ungefähr 100 MiB RAM verfügbar, ohne Swap. Der manuelle Schritt `prepare-memory.sh` wurde inzwischen ausgeführt: 4 GiB Swap auf der Root-Partition sind aktiv; zuletzt rund 1,2 GiB RAM verfügbar. Kein Anwendungsdienst wurde dafür neu gestartet. Der SSH-Benutzer traver besitzt weiterhin keine passwortlosen sudo-Rechte für die Nginx-Aktivierung.

## Rückweg

Vor Entfernung historischer Chunks: neue Worker stoppen, vorherige Nginx-Konfiguration zurückspielen und Nginx sanft neu laden. Nach Entfernung: die neue Lese-API weiterbetreiben, bis die geprüften Originalarchive zurückgespielt sind. Nur das Frontend kann unmittelbar auf die vorherige Version zurückgeschaltet werden. Archive werden nicht automatisch gelöscht.

## Prüfung

- 18 gezielte Tests gegen echte lokale TimescaleDB bestanden: komprimierte Chunks, parallele Einlieferung, verspätete Daten, Wiederholungen, Auflösungswechsel, gesperrte Chunks, beschädigte Backups, Mandantentrennung, WAV-Samples, vollständige Rücksicherung inklusive p05/p95 sowie Schutz vor versehentlicher Tabellenlöschung durch spätere Migrationen.
- Weitere 230 Backendprüfungen bestanden; drei davon sind auch in den 18 gezielten Tests enthalten. Damit 245 unterschiedliche Backendprüfungen.
- Frontend: Typprüfung, Lint, 22 Tests und beide Produktionsbuilds bestanden. Desktop und Mobilbreite mit einer echten Production-Stichprobe vom 15. September visuell geprüft.
- Images für linux/amd64 gebaut. API-Image startet lokal innerhalb des 192-MiB-Limits und verweigert nicht authentifizierte Zugriffe mit 401.
- Staging: getrennte Dienste laufen; Authentifizierung, Daten, CSV und 760 originale WAV-Samples im Endtest geprüft. Da Staging vorher keine Gateway-WAVs enthielt, wurde ein isolierter Prüfsensor mit bekannten Signalen angelegt.
- Staging-Verdichtung: 60 Sekundenmesswerte im Zehnminutenfenster, bekannter Ausschlag 1200 mV erhalten, korrekte Abdeckung 10 Prozent.
- Staging-Rücksicherung: 60 Originalzeilen aus dem auf dem Server erzeugten Archiv unverändert in eine temporäre Tabelle zurückgespielt. Keine laufenden Messwerte verändert.
- Alle bisherigen Backend-, Gateway-/Direct-, Speicher- und Frontend-Dienste behielten ihre Startzeitpunkte.

## Bereitstellungsstatus

Staging: Paket und Images unter `/home/traver/greenmind-visual-staging`. Neue Tabellen und drei getrennte Dienste sind eingerichtet. Historische Entfernung ist weiterhin deaktiviert. Der öffentliche Nginx-Proxy wurde noch nicht umgeschaltet. Der Nginx-Entwurf wurde mit temporären Loopback-Prüfports und Testzertifikat erfolgreich geprüft.

Die zusätzliche Prüfung des vollständigen Serverschemas fand eine zu enge Spaltennamenprüfung bei der Rücksicherung (p05/p95). Sie ist korrigiert; API-/Worker-Image `20260915-2` enthält diesen Fix und den Schutz der separat verwalteten Tabellen vor späterer Autogenerierung von Löschmigrationen. Dieses Image ist auf Staging installiert. Der vollständige Endtest und die Ausschlagsprüfung wurden damit erneut erfolgreich ausgeführt; die Aktivierungsfreigabe ist wieder vorhanden.

Production: getrenntes Frontend-Image auf Main-Basis lokal fertig. Die automatische Freigabeprüfung hat die Production-Übertragung abgelehnt, weil sie die Staging-Prüfung und ausdrückliche Production-Freigabe verlangt. Kein Production-Paket übertragen; Production-Daten unverändert.

Ausstehend: öffentliche Staging-Aktivierung mit sudo, externe Prüfung und anschliessende gezielte Bereinigung des isolierten Prüfsensors. Dessen IDs und S3-Schlüssel sind ausschliesslich in `fixture.json` dokumentiert; `cleanup-staging-fixture.py` entfernt nur diese Datensätze/Objekte. Erst nach externer Prüfung darf der Pruning-Marker gesetzt werden. Production benötigt zusätzlich die angefragte ausdrückliche Freigabe.

## Ausführung nach Speicherfreigabe

1. Nur das Staging-Imagearchiv laden, dann `start-standby.sh` ausführen. Das Skript verweigert den Start ohne mindestens 2 GiB freie Speicherreserve und stoppt keine vorhandenen Dienste.
2. `validate-standby.sh` prüft reale Daten, Authentifizierung, Original-WAV-Ausschnitt, CSV und unveränderte Startzeitpunkte der alten Dienste. Erst dieser Erfolg erzeugt die Aktivierungsfreigabe.
3. Mit sudo `python3 activate-proxy.py` ausführen. Alte Proxy-Konfiguration wird gesichert; Änderungen am Ausgangsstand führen zum Abbruch. Fehler beim Nginx-Test führen zur Rücksicherung.
4. Öffentliche, authentifizierte Endpunkte und Frontend prüfen. Dann `state/enable-pruning` anlegen. Ohne Marker werden historische Quelldaten auch bei laufendem Worker nicht entfernt.
5. Worker-Status und Archivwachstum beobachten. Nachbearbeitung ist gedrosselt und kann lange dauern; `paused_for_host_load` bedeutet, dass laufender Betrieb Vorrang erhält. Noch keine Aussage über insgesamt freigegebenen Speicher treffen.
