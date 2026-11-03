# Gateway sicher entfernen – Schritt 7

Stand: 03.11.2026. Implementiert und automatisiert geprüft; Einführung separat.

## Verhalten

„Gateway entfernen“ deaktiviert das Gateway atomar, löscht dessen API-Schlüssel
und markiert zugehörige Sensoren als `disconnected`. Der aktive Gerätebestand und
die Gateway-Anzahl der Zonen werden aktualisiert. Wiederholtes Entfernen ist
unschädlich; ein fehlendes oder nicht zugängliches Gateway liefert 404.

Gateway- und Sensor-IDs, Zonenbezüge, letzte Kontaktzeiten, Pflanzenzuordnungen,
Messungen, WAV-Metadaten und Originaldateien bleiben erhalten. Sensoren bleiben
in der Messansicht unter „Getrennt“ auswählbar, auch bei älterem letzten Kontakt.
Diese Funktion ist keine Datenlöschung und aktiviert keine Aufbewahrungsfristen.

## Schnittstelle und Widerruf

- Neu: `POST /api/v1/gateways/{gateway_id}/remove` (204).
- Bisheriges `DELETE /api/v1/gateways/{gateway_id}` verwendet dieselbe sichere Operation.
- Nur Eigentümer/Administratoren der berechtigten Organisation/Zone dürfen entfernen.
- Cookie-Anfragen benötigen die konfigurierte Frontend-Origin; Bearer-Clients bleiben kompatibel.
- Neue Geräte-Authentifizierungen nach abgeschlossenem Entfernen scheitern auch mit zuvor gecachten Schlüsseln.
- Bereits authentifizierte laufende Anfragen können abschliessen; bereits gelesene Daten sind nicht rückrufbar.
- Die Gateway-Zeilensperre serialisiert Entfernung und bestehende Neu-Provisionierung.
- Das deaktivierte Gerät kann sich nicht automatisch erneut registrieren; es kann kein neues Sensor-Move-Ziel sein.
- Keine Datenbankmigration: vorhandene Felder `is_active` und `api_key_hash` genügen.

## Oberfläche und Kompatibilität

Beide Einstiegspunkte (Überblick und Gateways) verwenden denselben zugänglichen
Bestätigungsdialog in Deutsch, Englisch, Französisch und Italienisch. Name,
Folgen und Datenerhalt werden vor dem Bestätigen angezeigt. Während der Anfrage
sind weitere Bestätigungen und Schliessen gesperrt. Fehler lassen die Bestätigung
offen. Ein erfolgreicher Widerruf mit fehlgeschlagener Listenaktualisierung erhält
einen eigenen Hinweis; „Erneut laden“ wiederholt nur die Abfrage.

Das neue Frontend verwendet ausschliesslich den neuen POST-Endpunkt. Ein alter
Server antwortet 404; es gibt keinen Rückfall auf dessen möglicherweise noch
physisch löschenden DELETE-Endpunkt. Backend zuerst ausliefern. Keine automatische
Reaktivierung und kein Neuanlegen als Rückfall; beide würden Identitäten verfälschen.

## Prüfungen

Backend-Tests reproduzieren die frühere physische Löschung und prüfen Datenerhalt,
Historienzugriff, Wiederholung, Rollen/Zonen, Cookie-Origin, Transaktions-Rollback,
Status und Schlüsselwiderruf für moderne, Digest- und ältere bcrypt-Schlüssel.
PostgreSQL prüft paralleles Entfernen, Konkurrenz zur Neu-Provisionierung und
den Widerruf bei bereits geladenen ORM-Objekten. Frontend-Tests prüfen beide
Einstiegspunkte, vier Sprachen, alte Server, Fehler, Aktualisierungswiederholung,
Doppelklicks, verspätete Antworten und historische Sensorauswahl.

GitHub führt die vollständige Veröffentlichungs-CI sowie gezielte Prüfungen der
aktuellen Receiver-Variante aus. Ergebnisse und Links stehen im jeweiligen PR.

## Einführung

Zunächst Staging mit Backend vor Frontend, danach separat freigegebene Produktion.
Keine Firmwareänderung, kein OTA und kein Eingriff in Peters vier Büroboxen oder
GmLive. Direct-Aufnahmen, Archive und produktive Datenlöschung sind unverändert.
