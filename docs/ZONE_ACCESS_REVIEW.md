# Zonenweise Messungen und Zugangsrechte

Stand: 22. September 2026. Lokale Änderungen auf Basis von develop 61cce4e.
Production-Veröffentlichung vom Benutzer am 22. September 2026 freigegeben. Vorbereitung und Abnahme erfolgen vor der Umschaltung.

## Verhalten

- Messungen und Sensoren sind nach freigegebenen Zonen gruppiert. Gateway- und Direct-Geräte erscheinen in ihrer jeweiligen Zone; inaktive Gateway-Sensoren bleiben dort aufklappbar.
- Der Zonenfilter und Links aus der Zonenübersicht öffnen die passende Gruppe. Bestehende Signaldiagramme, Zeitauflösungen und Originalaufnahmen bleiben erhalten.
- Eigentümer und Administratoren sehen sämtliche Zonen ihrer eigenen Organisation. Mitglieder sehen ausdrücklich freigegebene Zonen. Diese Rollenannahme wurde zur Rückmeldung vorgelegt.
- Unter Zonen → Zugänge verwalten können Eigentümer/Administratoren einer Person mehrere Zonen zuweisen. Eine leere Auswahl entzieht alle Zonenzugänge. Änderungen werden protokolliert.
- Gary/Peter wurden keinem Konto automatisch zugeordnet. Die konkreten Benutzer und Zonen müssen beim Rollout geprüft und freigegeben werden.

## Serverseitige Durchsetzung

Eine reine Oberflächenfilterung würde den Zugriff über direkte URLs offenlassen. Eine gemeinsame Zonenprüfung schützt deshalb Zonen, Gateway-/Sensorlisten, Messwerte, CSV, WAV, Pflanzen samt Sensorhistorie, Direct-Gerätelisten und Direct-Messdaten. Dashboard-WebSockets prüfen aktuelle Berechtigungen vor dem Versand; ein Sensorumzug und ein Rechteentzug beenden den betreffenden Zugriff. Die Prüfung hat eine begrenzte Wartezeit.

Provisioning aus dem Dashboard ist auf freigegebene Zonen begrenzt. SMS-Empfänger müssen aktiv, verifiziert und für die Zone berechtigt sein. Geräteanmeldung, Gateway-Schlüssel, Direct-Gerätetoken, Empfangsprotokolle, WAV-Erzeugung und Komprimierungsstufen bleiben unabhängig von Benutzerfreigaben. Bestehende öffentliche Beobachtungslinks verwenden weiterhin ihre eigene Zugriffsmethode.

## Migration und Veröffentlichung

Migration 0023 ergänzt die Tabelle zone_access. Sie übernimmt die bisherigen Organisationszugänge bestehender Mitglieder ausdrücklich für alle zu diesem Zeitpunkt vorhandenen Zonen. Neue Mitglieder und später angelegte Zonen erhalten keine automatische Freigabe. Das verhindert unbeabsichtigtes Aussperren beim technischen Wechsel; die gewünschten engeren Freigaben sind danach gezielt zu speichern.

Vor einer Veröffentlichung sind der exakte Diff, Zielumgebung, Tests, Kompatibilität und Rücknahme gemeinsam zu prüfen. Zuerst develop/Staging, anschließend Browser- und Sensortests; Production benötigt die separate Freigabe für diese Änderungen.

Die Migration muss vor den neuen Codepfaden installiert sein. Legacy-Dashboard-API, Visualisierungs-API, Direct-Geräteliste und Frontend müssen dieselbe Zonenpolitik anwenden. Das bestehende isolierte Dashboard-Release routet nur einen Teil der API-Endpunkte um; ein bloßer Frontend-/Visualisierungs-Upload würde deshalb die vollständige Rechteprüfung nicht herstellen. Der Operator unter deploy/zone-access bereitet dafür parallele Application-, Visualisierungs-, Direct-Metadaten- und Frontend-Instanzen vor. Gateway-HTTP-Anfragen wechseln beim Proxy-Reload auf die geprüfte neue Application-API. Die bisherigen Instanzen bleiben gestartet; Geräteadressen, Schlüssel und Datenformate ändern sich nicht. Der Fernsteuerungsschutz bleibt semantisch identisch, einschliesslich 410-zu-503. Direct-Chunks, Pairing und Assembler bleiben auf dem bisherigen Direct-Empfänger. Die neuen Rechte dürfen erst als wirksam gelten, wenn alle öffentlich erreichbaren Lesepfade getestet wurden.

Die additive Tabelle verträgt den alten Code. Nach Einschränkung von Benutzerrechten würde ein Rückwechsel auf alte Organisationsprüfungen jedoch Zugriff erweitern. Deshalb weder die Tabelle entfernen noch alte unbeschränkte Dashboard-Routen als automatisches Rollback öffnen. Stattdessen die betroffenen Dashboard-Zugriffe vorübergehend sperren und die korrigierte Rechteprüfung wiederherstellen; Geräteempfang weiterbetreiben. Der automatische Datenbank-Downgrade verweigert daher das Löschen der Berechtigungstabelle.

## Prüfung

Die Prüfung umfasst mehrere und keine Freigaben, Fremdorganisationen, erratene Daten-/Export-/WAV-URLs, Direct-Zugriff, Rechteentzug bei Live-Verbindungen, Sensorumzüge, Pflanzenzuordnungen und Gateway-/Direct-Uploads trotz Rechteentzug. Die Migration wurde in einem eigenen Schema einer lokalen PostgreSQL-Testdatenbank geprüft, einschließlich Bestandsübernahme, neuen Benutzern und Löschbeziehungen.

Die konkreten Testergebnisse stehen in ZONE_ACCESS_VALIDATION.md. Live-Rollout, echte Gary-/Peter-Anmeldung und Browser-Abnahme auf Staging wurden für diese Änderung noch nicht durchgeführt. Die Live-Ergebnisse des Veröffentlichungsauftrags werden nach der Abnahme separat festgehalten.
