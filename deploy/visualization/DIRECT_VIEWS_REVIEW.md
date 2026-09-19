# Direct-Messungen auf Staging sichtbar machen

## Nachgewiesene Ursache

Der Direct-Empfänger speichert Messblöcke und verifizierte WAVs in seiner eigenen PostgreSQL-Datenbank und seinem eigenen Objektspeicher. Die bisherige Direct-Karte zeigt ausschliesslich den Empfangsstatus. Die vorhandenen Diagramme lesen dagegen Gateway-Sensoren. Damit fehlen die Direct-Werte in der Messansicht, obwohl die Aufnahme funktioniert.

## Änderungen

- Ein eigener gedrosselter Worker erzeugt Direct-Diagrammwerte in drei neuen `direct_visual_*`-Tabellen der bereits isolierten Direct-Datenbank. Bestehende Empfangstabellen, Sensorzuordnungen und WAV-Dateien werden nicht verändert.
- Aktuelle Empfangsblöcke werden anhand ihrer Prüfsumme ausgewertet. Bereits archivierte Werte werden aus verifizierten WAVs rekonstruiert. Dieselbe Aufnahme wird atomar ersetzt statt mehrfach gezählt.
- Auflösung entsprechend der bisherigen Dokumentation: jüngste 24 Stunden sekundengenau, anschliessend bis Tag 7 minutengenau, ältere Werte in Zehnminutenfenstern. Ein angefangener Zehnminutenabschnitt darf die feinere Auflösung kurz länger behalten. Mittelwert, Minimum, Maximum, RMS, Streuung, Anzahl und Abdeckung stammen aus allen tatsächlich vorhandenen Samples.
- Die PCM16-Rückrechnung entspricht der bestehenden Kalibrierung `3300/32767 mV` pro Zähler. PCM24-Kanäle bleiben als ADC-Zähler gekennzeichnet. DUAL-Daten werden als separate Vergleichsmessung dargestellt.
- Unter „Messungen & Sensoren“ können Direct-Sensoren ausgewählt werden. Der erste Sensor wird direkt geöffnet. Zeiträume, Diagramm, Originalsignal, CSV und geprüfte WAV-Downloads stehen zur Verfügung. Empfang und Messansicht aktualisieren sich alle zehn Sekunden.
- Anmeldungen und Organisationen werden anhand der bestehenden Konten und Zonen geprüft. Fremde Sensoren liefern 404; fehlende Anmeldung 401. Keine Geräte- oder Speicherzugangsdaten werden an den Browser gegeben.

## Ziel und laufender Betrieb

Nur `test.green-mind.ch`. Die vorhandene Visualisierungs-Lese-API und ihr Frontend werden auf neue Images umgestellt; ein eigener Direct-Aufbereitungsworker kommt hinzu. Die bestehenden Gateway-/Direct-Empfänger, WAV-Konverter, Datenbanken, Legacy-Verdichtung und sämtliche Production-Dienste bleiben laufend. Keine Änderung am Nginx nötig; die vorhandene `/api/v1/visualization/`-Route wird genutzt. Beim Austausch von Lese-API und Frontend ist eine kurze Unterbrechung einzelner Dashboard-Abfragen möglich.

Der neue Worker ist auf 0,2 CPU und 256 MiB RAM begrenzt. Er pausiert bei hoher Serverlast, verarbeitet aktuelle Sensoren fair und reserviert Kapazität für historische Nachbearbeitung. Er entfernt nur ersetzte, reproduzierbare Diagrammzeilen. Er löscht keine Quelldaten und aktiviert keine WAV-Retention.

## Prüfung und Rückweg

Lokal bestanden: 56 Backend-Prüfungen inklusive echter PostgreSQL-Integrationsprüfungen und Legacy-/Direct-Kompatibilität, 24 Frontend-Tests, Typprüfung, Lint und beide linux/amd64-Builds. Die Abhängigkeiten des API-Basisimages stimmen per SHA256 mit der unveränderten requirements.lock überein. Nach Freigabe folgt die authentifizierte Prüfung von echter Direct-Serie, Originalsignal, CSV, WAV und unveränderten Startzeiten aller Empfangsdienste.

API-Image: `sha256:b4a85d77ef387ea926ab6ffd648e7e8cce5a34c41a957331b24d1cf9d1431ada`. Frontend-Image: `sha256:2eaaba7bc581ae843c13b3b49dd2bd2f546a8c08e6c4c9ba3a7d1a59dad0af06`.

Aktivierung: `start-direct-views.sh` zusammen mit `direct-views.yml`, `prepare-direct-views.py` und beiden geprüften Images. Künftige gezielte Wartung verwendet `docker compose -f compose.yml -f direct-views.yml`.

Rückweg: neuen Worker stoppen; Lese-API und Frontend mit der bisherigen `compose.yml` gezielt wiederherstellen. Die zusätzliche Konfiguration und die neuen Diagrammtabellen können bestehen bleiben. Der Empfang und die originalen WAVs werden durch die Rücknahme nicht berührt. Kein vollständiger Develop-/Main-Release und kein Production-Push.
