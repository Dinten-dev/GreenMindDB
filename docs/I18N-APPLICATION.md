# Anwendung in vier Sprachen

Stand 03.11.2026: Schritt 12 vervollständigt Deutsch, Englisch, Französisch und
Italienisch auf Basis von Schritt 11. Der angemeldete Bereich wurde bereits in
Schritt 6 vorgezogen. Die getrennten Entwicklungsverläufe bleiben erhalten.

## Umfang

Navigation, Anmeldung, Konten, Pflanzen, Messungen, Zonen und sämtliche vorhandenen
Administrations-, Firmware- und Flottenwerkzeuge gehören zum Sprachumfang.
Auch Exportdialog, Livefenster, Rail-Hinweise, Mark-Knopf und Aufnahmeprüfungen sind
übersetzt. Warnungen, Fehler und Erfolgsmeldungen verwenden die gewählte Sprache.

Die öffentlichen Seiten Über uns, Plattform, Technologie, Forschung, Kontakt,
Zugangsanfrage und Impressum sind vollständig eingebunden. Personen- und Firmennamen,
Adressen, Publikationstitel, Autoren, DOI, Links und eingegebene Daten bleiben Daten.
Vorhandene Forschungszusammenfassungen und Kategorien sind übersetzt; Quellen und
Studiendaten wurden dabei nicht neu validiert oder erweitert. Zusammenfassungen
werden als solche bezeichnet. Die Forschungsübersicht unterscheidet Veröffentlichungen
und gekennzeichnete Preprints. Formate, Mehrzahlformen und Zugänglichkeitsbeschriftungen
werden ebenfalls geprüft. Englische Formulierungen und Navigationsbegriffe wurden
vereinheitlicht. Rechtstexte sind Übersetzungen der bestehenden Inhalte.

Seitentitel, Beschreibungen und Sprachkennungen folgen der Routen-Sprache.
Auch angemeldete Besucher können auf öffentlichen Seiten die Sprache wechseln.
Seite, Abfrageparameter und Sprunganker bleiben dabei erhalten.

## QR-Bewertungen

Die bestehende Pflanzenbewertung läuft unter /{locale}/observe/{publicId} in allen
vier Sprachen. Alte /observe/{publicId}-Links bleiben gültig und werden auf eine
unterstützte Sprache weitergeleitet; ohne Sprachhinweis ist dies Deutsch.
Die Locale-Middleware lässt diese alten QR-Einstiege zur Weiterleitung durch.
Neue QR-Druckansichten verwenden die gewählte Sprache. Vor Beginn einer Bewertung
kann die Sprache gewählt werden. API-Routen, Sessionablauf, Bewertungsschlüssel,
Enum-Werte und gespeicherte Benutzereingaben bleiben unverändert.
Bewertungsseiten behalten noindex/nofollow und ihre reduzierte Oberfläche.

## Sprachwahl und Daten

Jede neu geöffnete Messansicht startet weiterhin mit 24 Stunden. Ein gezielter
Sprachwechsel erhält die laufende Live-Ansicht einmalig über den bestehenden
kurz gültigen Tab-Vermerk. Sensor, freigegebene Zone und Direct-Auswahl bleiben
Ansichtsparameter; auch die gewählten Livefenster für Gateway und Direct bleiben
beim Sprachwechsel erhalten. Neue Öffnungen beginnen wieder mit 24 Stunden.
Die wiederhergestellte Auswahl wird gegen tatsächlich zugängliche Geräte geprüft.

Unbekannte Serverprosa wird nicht als unübersetzte Benutzermeldung ausgegeben.
Technische Originaldiagnosen bleiben im aktuellen Empfänger bei Bedarf eingeklappt
zugänglich. Der aktuelle Anmeldedialog behält seinen Zugangsanfrage-Link und erhält
keine freie Registrierungsfunktion oder neue Einschränkung bestehender Passwörter.
Der gemeinsame API-Client und sämtliche serverseitigen Berechtigungen bleiben bestehen.

## Pflege

- Sprachkataloge: frontend/messages/{de,en,fr,it}.json.
- App enthält die feststehenden Texte der angemeldeten Anwendung.
- Public enthält die feststehenden öffentlichen Texte.
- Metadata und ObservationMetadata enthalten sprachabhängige Seitentitel.
- app-message-keys.json und public-message-keys.json halten stabile Zuordnungen.
- Bestehende IDs nicht neu nummerieren oder anders belegen.
- Neue Texte benötigen Einträge in allen vier Katalogen.
- useAppText und usePublicText nur für feststehende Texte verwenden.
- Personen-, Pflanzen- und Firmennamen sowie Diagnosedaten nicht übersetzen.

## Prüfung und Einführung

Automatische Tests prüfen Katalogvollständigkeit, ICU-Platzhalter, feste JSX-Texte,
Zugänglichkeitsbeschriftungen und bekannte dynamische Textlisten. Komponententests
prüfen öffentliche Seiten, Formulare, Forschungsbeschreibungen, Anmeldung und den
vollständigen QR-Bewertungsablauf in vier Sprachen. Die übermittelten Enum-Werte und
Benutzereingaben bleiben identisch. Messansichts-Tests prüfen den erhaltenen
Gateway-/Direct-Zeitraum beim Sprachwechsel. Reale lokale HTTP-Prüfungen prüfen
Sprachkennung, Seitentitel, noindex und alte QR-Weiterleitungen beider Builds.
Nach dem Build prüft npm run test:routes zusätzlich je 20 reale Loopback-Anfragen
auch in GitHub Actions. Der Testserver wird danach beendet.

Eine visuelle Browserabnahme bleibt offen, solange Browsernutzung in den
Work-Einstellungen deaktiviert ist. Komponenten-, HTTP- und Buildprüfungen ersetzen
keine visuelle Abnahme. Vor Einführung auf Staging auch reale Berechtigungen und
Geräte prüfen. Kein Merge, Deployment, Löschlauf oder OTA gehört zu diesem Schritt.
Peters Büroboxen, gmlive und die Konfiguration des USB-Testgeräts bleiben unverändert.
