# Anwendung in vier Sprachen

Deutsch, Englisch, Französisch und Italienisch gelten auch nach dem Login.

## Umfang

- Navigation, Konto und Übersicht unterstützen alle vier Sprachen.
- Pflanzen, Zonen, Sensoren und Gerätekopplung sind übersetzt.
- Firmen, Benutzer, Zugangsverwaltung und Einladungen sind eingeschlossen.
- Firmware, Gateway-Flotte, Aktionen, Tabellen und Prüfprotokolle sind eingeschlossen.
- Diagrammtexte, Exportdialoge und Aufnahmeprüfungen sind eingeschlossen.
- Bestätigungen, Erfolgsmeldungen und Fehler sind sprachabhängig.
- Datumsanzeigen und relative Zeitangaben verwenden die gewählte Sprache.
- Mehrzahlformen verwenden vollständige ICU-Nachrichten statt angehängter Buchstaben.
- Aktuelle Receiver-Funktionen bleiben im eigenen Entwicklungsverlauf erhalten.

## Sprachwahl und Daten

- Italienisch ist in Sprachkonfiguration, Routen und Login eingebunden.
- Sprachwahl ist auch innerhalb der Anwendung erreichbar.
- Wechsel erhält Seite, erlaubte Sensor-/Zonenauswahl und gewählten Zeitraum.
- Direct-Auswahl und Direct-Zeitraum bleiben ebenfalls erhalten.
- Diagrammanker und bestehende Abfrageparameter bleiben erhalten.
- Zurückkehrende Auswahl wird gegen tatsächlich zugängliche Geräte geprüft.
- Gerätekennungen und Zeitfenster sind Ansichtsparameter, keine Zugangstokens.
- Benutzernamen, Pflanzennamen, Messwerte und Originaldiagnosen bleiben Daten.
- Technische Protokollkennungen und Dateiinhalte werden nicht umgeschrieben.
- Datenbankwerte, Berechtigungen, Aufnahmen und Exportinhalte bleiben unverändert.

## Pflege und Prüfung

- Sprachkataloge liegen in `frontend/messages/{de,en,fr,it}.json`.
- `App` enthält die feststehenden Texte der angemeldeten Anwendung.
- `app-message-keys.json` ordnet vorhandene Ausgangstexte stabilen IDs zu.
- Bestehende IDs nicht neu nummerieren oder anders belegen.
- Neue Oberflächentexte benötigen Einträge in sämtlichen vier Katalogen.
- `useAppText` übersetzt ausschliesslich feststehende Texte und bekannte Statuswerte.
- Unbekannte Serverprosa erhält verständliche lokalisierte Fehlerhinweise.
- Gemeinsamer API-Client unterscheidet Sitzungsablauf und fehlende Rollenberechtigung.
- Unternehmenslöschung erklärt bestehende Zuordnungsblockaden weiterhin ausdrücklich.
- Tests prüfen sämtliche Kataloge, Platzhalter und feststehende Anwendungstexte.
- Integrationstests prüfen Kontoseite, Navigation, Fehler und Status in vier Sprachen.
- Auswahlwiederherstellung prüft Geräteberechtigung und gespeicherten Zeitraum.
- Zusätzliche Receiver-Tests prüfen übersetzte Aufnahmequalität und Zahlenformate.
- Lokal bestanden: 108 Frontendtests der Veröffentlichungslinie.
- Lokal bestanden: 128 Frontendtests des aktuellen Receivers.
- Lint, Formatierung, Typprüfung und beide Produktionsbuilds bestanden.
- Browserprüfung erfolgt lokal mit synthetischen Konten und Testdaten.
- Beide Varianten bauen auf der geprüften Login-Korrektur auf.

## Einführung

- Änderungen zuerst gemeinsam mit der Login-Korrektur auf Staging prüfen.
- Sprachwechsel auch mit realen Berechtigungen und Geräten abnehmen.
- Die übrigen öffentlichen Inhaltsseiten bleiben Teil von Schritt 12.
- Kein Merge, Deployment, produktiver Löschlauf oder OTA enthalten.
- Peters Büroboxen und GmLive bleiben unverändert.
