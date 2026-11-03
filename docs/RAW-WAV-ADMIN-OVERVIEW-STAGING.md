# WAV-Archiv in der Administration

Nur aktive, bestätigte Benutzer der konfigurierten Admin-Allowlist sehen die Übersicht.
Staging bleibt ohne Production-Verbindung; manuelles Kopieren liefert dort HTTP 409.

Production erhält über einen privaten Unix-Socket Statuswerte des bestehenden
Kopierdienstes: verifizierte Dateien, RAM, Status und Storage-Box-Belegung. Die
Kopierrate misst neu verifizierte Bytes pro Messintervall, nicht Netzwerkverkehr.
Der Status wird alle fünf Sekunden gesammelt, die Box-Belegung jede Minute.
Ein unbekannter Gesamtrückstand wird als unbekannt angezeigt, nicht als Null.
Die Warteschlange zählt Referenzen und ist keine vollständige Inventarliste.

Der Knopf unter der Storage Box startet nur `greenmind-raw-copy.service`.
Laufende Anforderungen werden zusammengeführt; mindestens 60 Sekunden Abstand.
Vor jedem Start prüft der Host alle Lösch-/Retention-Schalter auf `false` und den
root-eigenen Copy-only-Launcher. Es gibt keine Löschfunktion in dieser Schnittstelle.

## Installation

`deploy/archive-monitor/bridge.py` root-eigen unter `/opt/greenmind/archive-monitor/`
installieren, die mitgelieferte systemd-Unit installieren und starten. Den Ordner
`/run/greenmind-archive-monitor` ausschliesslich in den isolierten Admin-Container
unter demselben Pfad read-only einbinden. `ARCHIVE_MONITOR_SOCKET` dort auf
`/run/greenmind-archive-monitor/bridge.sock` setzen. Socket-Gruppe: Container-GID 10001.
Keinen Docker-Socket, keine Storage-Zugangsdaten in den Admin-Container einbinden.

Empfangsdienste, Datenbanken und Nachtlauf werden nicht neu gestartet. Die
Löschungspolicy bleibt deaktiviert. Rücknahme: bisherige Admin-/Frontend-Routen
wiederherstellen und ausschliesslich den neuen Monitor stoppen.
