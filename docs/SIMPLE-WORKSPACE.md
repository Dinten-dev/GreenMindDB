# Schritt 11: Einfache Messansicht

Softwarekandidat vom 03.11.2026 auf Schritt 10, getrennt für Publikationshistorie
und aktuellen Empfänger. Kein Merge, Deployment oder Firmware-Rollout.

Die Hauptnavigation enthält Überblick, Pflanzen, Messungen und Verwaltung.
Unter Verwaltung bleiben Zonen und Gateways für angemeldete Konten erreichbar.
Firmware und Flottenwerkzeuge behalten ihre bestehende Rollenprüfung; die
zusätzliche Administration behält ihre gesonderte Berechtigungsprüfung.
Die bestehenden Zielrouten und serverseitigen Zugangskontrollen bleiben bestehen.
Mein Konto ist weiterhin im unteren Navigationsbereich erreichbar.

Jede neu geöffnete Messansicht startet mit 24 Stunden, auch bei alten Bereichs-
oder Live-URLs. Eine vom Sprachschalter ausgelöste Navigation erhält die laufende
Live-Ansicht einmalig: ein kurz gültiger, pfadgebundener Tab-Vermerk wird nach
Verwendung entfernt. Gerätewahl und freigegebene Zone bleiben im URL-Zustand.
Beim Schliessen und erneuten Öffnen eines Geräts gilt wieder 24 Stunden.
Der Sprachwechsel erhält auch das ausgewählte Direct-Livefenster.

Die Messansicht zeigt nur 24 Stunden und Live. Direct-Live behält die echte
12,5-Hz-Vorschau, 30/60/180-Sekunden-Fenster, Signalalter, Rail-Prozent,
Rail-Zeitfenster, Aktualität und den direkt erreichbaren Mark-Knopf.
Übersteuerung wird als Messproblem erklärt, ohne Pflanzenstress abzuleiten.
Markierungen bleiben drei Minuten im gemeinsamen flüchtigen Livepuffer und
werden nicht exportiert. Das bestehende Verfahren für Viewer-Leases bleibt erhalten.

Der ältere Gateway-Pfad verwendet weiter seine bestehende Abfrage/WebSocket-
Übertragung; er erhält dieselben drei sichtbaren Livefenster und Signalalter.
Fehlende Rail-Prozentwerte und Markierungsfähigkeit werden ausdrücklich benannt.
Aus gemittelten Gateway-Werten werden weder Rail-Prozent noch Direct-Fähigkeiten
erfunden. Ausgeblendete Tabs pausieren Abfrage und WebSocket. Der 380-Hz-Gateway-
Vertrag bleibt unverändert; die Oberfläche migriert keine Geräte auf Direct.

Exportieren ersetzt sichtbare Aufnahmelisten und separate Download-Bereiche.
Der native modale Dialog bietet Gerätewahl, genaue UTC-Grenzen und CSV oder WAV.
Die Vorauswahl eines geöffneten Diagramms verwendet dessen UTC-Grenzen.
Historische Verläufe können für den gewählten Zeitraum im Dialog angezeigt werden,
ohne den 24-Stunden/Live-Hauptmodus zu erweitern. CSV bleibt die verdichtete
Übersicht; WAV bleibt die unveränderte Originalaufnahme. Gateway-WAVs werden über
die bestehende ZIP-Route geladen. Direct zeigt nach ausdrücklicher Auswahl die
neuesten 100 passenden Originalaufnahmen; ältere Dateien bleiben durch Eingrenzen
des Zeitfensters erreichbar. Überschneidende WAVs können über die gewählten
Grenzen hinausreichen. Der aktuelle Empfänger behält zusätzlich seine vorhandenen,
explizit eingeschalteten Archivexporte und Vollständigkeitsprüfungen im Dialog.
Die Publikationshistorie bekommt keine fremde Archivkonfiguration.

Schnittstellen akzeptieren additive explizite Zeitgrenzen, prüfen beide Grenzen,
Zeitzone, positive Dauer und maximal zehn Jahre. Grenzen sind links einschliessend,
rechts ausschliessend. Alle Exportbegrenzungen, Tenant-/Zonenprüfungen, Hashprüfungen
und Archivleser bleiben wirksam. Das Empfangssystem und gespeicherte Daten werden
nicht verändert. Technische Erläuterungen und Geräteeinstellungen sind eingeklappt;
Warnungen über fehlende Zeitanker, Datenlücken oder veraltete Livewerte bleiben sichtbar.

Neue Texte sind auf Deutsch, Englisch, Französisch und Italienisch vorhanden.
Tests prüfen die vier Hauptziele, Verwaltungsrollen, Standardzeitraum, einmalige
Sprachübergabe, leere/ungültige Exportfenster, Gerätewechsel, abgebrochene Anfragen,
Berechtigungsfehler, Millisekundengrenzen und historischen Zugriff.
PostgreSQL prüft dieselben Grenzen und die bestehende Eigentumsprüfung.
Eine visuelle Browserabnahme ist offen: Browsernutzung ist in den Work-Einstellungen
deaktiviert. Komponentenprüfungen und Produktionsbuild ersetzen diese Abnahme nicht.
Physische Live-Latenz und Firmware-Langzeitabnahme bleiben unverändert offen.
Peters Büroboxen, gmlive und die lokale Konfiguration des USB-Testgeräts bleiben unberührt.
