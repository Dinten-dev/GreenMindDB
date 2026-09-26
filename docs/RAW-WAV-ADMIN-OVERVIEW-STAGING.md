# WAV-Archiv in der Administration

Die Administration zeigt eine Archivübersicht mit geprüfter Dateizahl,
geschätztem Rückstand, Übertragungsrate, RAM und Storage-Box-Belegung.
Der manuelle Kopierknopf steht im Storage-Box-Bereich unter der Belegungsanzeige.

## Zugriff und Staging-Grenze

- Die bestehende Admin-Allowlist und der aktive, bestätigte Account schützen UI und API.
- Staging zeigt für nicht angebundene Messwerte `–` und meldet den Dienststatus.
- Der Kopierknopf ist auf Staging deaktiviert.
- `POST /api/v1/administration/archive/copy` antwortet auf Staging mit HTTP 409.
- Der Endpunkt startet weder Prozess noch Systemdienst und schreibt keine Dateien.
- Es werden keine Produktionsstatusdaten in die Staging-API eingebunden.
- Storage-Box-Daten ohne verlässliche Quelle erscheinen als nicht verfügbar, nie als 0 %.

## Production-Voraussetzung

Der bestehende Host-Kopierdienst ist noch nicht mit dieser API verbunden. Deshalb
bleibt die Übersicht auch auf Production zunächst ohne Live-Werte; die API lehnt
einen manuellen Start dort mit HTTP 503 ab. Vor Freischaltung sind eine getrennte,
geschützte Telemetriequelle und ein sicherer, idempotenter Trigger zum isolierten
Kopierdienst nötig. Ein Trigger darf nur Kopien erzeugen; lokale WAV-Dateien werden
nicht gelöscht. Diese Staging-Änderung aktiviert oder verändert den Nachtlauf nicht.

## Aktualisierung

Die Oberfläche fragt den Status alle fünf Sekunden ab. Alle Anzeigen zeigen die
Messzeit, sobald ein Statusdienst angebunden ist. Der fehlende Status wird als
nicht verfügbar angezeigt und nicht durch Beispiel- oder Produktionswerte ersetzt.
