# Administration: Speicher und Benutzer

Entwicklungsumgebung: `develop` → `https://test.green-mind.ch` (Staging).
Production wird durch diese Veröffentlichung nicht geändert.

## Bedienung

Freigegebene Konten sehen unter **Verwaltung → Administration** den Serverspeicher
und die neue Kachel **Kunden & Benutzer**. Die vorhandenen Verwaltungseinträge
und die Oberfläche normaler Benutzer bleiben erhalten.

Der Speicherstatus zeigt den belegten Anteil sowie den für Dienste verfügbaren
freien Speicher des konfigurierten Host-Dateisystems. Die Anzeige aktualisiert
sich jede Minute. Der Warnhinweis erscheint bei **weniger als 5 %**, nicht bei
exakt 5 %. Fehler beim Lesen werden als „nicht verfügbar“ gemeldet und nicht als
volle Festplatte. Es werden keine Dateien durchsucht oder gelöscht.

Die Benutzerverwaltung bietet Suche, seitenweise Listen, neue Konten,
Namens-/Telefonänderungen, Firmenzuordnung, bestehende Rollen, Zonenzugänge und
Aktivieren/Deaktivieren. Ein Firmenwechsel entfernt alte Zonenzugänge atomar.
Mitglieder erhalten nur explizit ausgewählte Zonen. Eigentümer und Administratoren
behalten ihr bestehendes rollenbasiertes Verhalten. Die Rolle „Administrator“
kann auch bestehende Firmware-/Gateway-Werkzeuge freigeben; sie ist keine rein
auf Zonen beschränkte Rolle. Die zentrale Administration selbst erfordert immer
den zusätzlichen Konfigurationseintrag.

Neue Konten sind administrativ bestätigt und können sich sofort anmelden. Ihr
Startpasswort benötigt mindestens zwölf Zeichen sowie Gross-/Kleinbuchstaben und
eine Zahl. Es wird nur gehasht gespeichert, niemals protokolliert, zurückgegeben
oder automatisch per E-Mail versendet. Die sichere persönliche Übergabe obliegt
dem Administrator. Keine automatische Passwort-Rücksetzung.

Zentrale Administrationskonten können hier nicht deaktiviert/umgehängt werden.
Der letzte aktive, bestätigte Firmenverantwortliche bleibt geschützt.
Benutzeränderungen werden ohne Passwörter im bestehenden Audit-Protokoll erfasst.

## Konfiguration

| Wert | Beispiel/Platzhalter | Bedeutung |
|---|---|---|
| `MANAGEMENT_ADMIN_EMAILS` | `<REQUESTER_EMAIL>,<PETER_EMAIL>` | Kommagetrennte, vollständige Login-Adressen. Leer deaktiviert die Funktionen. |
| `STORAGE_MONITOR_PATH` | `/host-storage` | Nur lesend eingebundenes leeres Verzeichnis auf dem tatsächlich überwachten Host-Dateisystem. |
| `STORAGE_WARNING_FREE_PERCENT` | `5` | Strikter Grenzwert; Werte über 5 werden abgelehnt. |

Die Anwendung hat kein separates unveränderliches Benutzernamenfeld.
Anzeigenamen sind selbst editierbar und dürfen keine Admin-Rechte vergeben.
Deshalb wird die konfigurierte Liste mit der bestätigten Login-E-Mail verglichen,
ohne Gross-/Kleinschreibung. Die Freigabe gilt ausschliesslich für aktive,
bestätigte Konten. Die Liste steht weder im Frontend noch im Quellcode.

Vor der Veröffentlichung Peters genaue Kontoidentität bestätigen. Fehlt sein
Konto in Staging, existiert dort noch kein persönlicher Zugang; Production-Konten
oder Passwörter werden nicht automatisch kopiert. Der vorhandene Staging-Admin
kann für die Entwicklungsabnahme separat konfiguriert werden.

## API

Alle Routen verwenden die bestehenden Cookie-/Bearer-Anmeldungen:

- `GET /api/v1/administration/capabilities`: nur eigene Freigabe, keine Adminliste.
- `GET /api/v1/administration/storage`: aktuelle Dateisystemstatistik.
- `GET /api/v1/administration/catalog`: Firmen und ihre Zonen.
- `GET /api/v1/administration/users?search=&offset=0&limit=50`: Benutzerliste.
- `POST /api/v1/administration/users`: neues Konto mit Firma/Rolle/Zonen.
- `PUT /api/v1/administration/users/{id}`: atomare Konto-/Firmen-/Zonenänderung.

Ausser `capabilities` sind alle Routen serverseitig auf die Freigabeliste begrenzt.
Nicht angemeldet: 401. Nicht freigegeben: 403. Fremde Firmenzonen: 422.
Gesicherte Konten, doppelte E-Mail oder letzter Firmenverantwortlicher: 409.
Die bestehenden Tabellen und Passwort-/Anmeldemechanismen werden wiederverwendet.
Keine neue Datenbankmigration nötig; die vorhandene Migration 0023 muss aktiv sein.

## Entwicklung ausrollen

1. Tests ausführen, exakten Commit nach `develop` pushen, vollständige CI abwarten.
2. `deploy/release/build-bundle.sh <ABSOLUTES_NEUES_BUNDLEVERZEICHNIS>` auf dem
   Entwicklungsrechner ausführen; zusätzlich `deploy/zone-access` aus demselben
   Commit ins Paket übernehmen. Archiv-/Image-Prüfsummen kontrollieren.
3. Nur dieses Paket übertragen. Kein vollständiges Compose-Neustarten.
4. Ein leeres Verzeichnis auf der überwachten Host-Partition anlegen. Es darf
   keine Serverdateien enthalten und wird ausschliesslich lesbar eingebunden.
5. Mit `deploy/zone-access/rollout.py prepare --environment staging` und dem
   exakten Commit, Backend-/Frontend-Image, neuen Release-Verzeichnis sowie
   `--management-admin-emails '<VERIFIZIERTE_EMAIL_LISTE>'`
   `--storage-monitor-directory '<LEERES_HOSTVERZEICHNIS>'` vorbereiten.
   Die regulären Parameter stehen in `docs/ZONE_ACCESS_REVIEW.md`.
6. `rollout.py start --directory <RELEASEVERZEICHNIS>` startet die isolierten
   Kandidaten. Bestehende Empfangsdienste und Production laufen weiter.
7. Mit einem ausdrücklich dafür angelegten Staging-Testadmin regulär anmelden.
   Speicherwerte gegen `statvfs` auf dem Host vergleichen. Konto anlegen,
   Firmenwechsel/Zonenentzug/Deaktivieren sowie Nicht-Admin-Abweisung testen.
   Testkonten anschliessend entfernen; keine realen Nutzerrechte verändern.
8. Exakte Proxy-Differenz, Staging-Abnahme und Rücknahme prüfen. Erst dann
   `sudo python3 .../rollout.py activate --directory <RELEASEVERZEICHNIS>`.
   Öffentliche Tests wiederholen und alle vorherigen Containerstartzeiten sowie
   die unveränderte Production-Konfiguration kontrollieren.

Rücknahme stellt das gespeicherte `before.conf` für **Staging** wieder her und
lädt Nginx neu; vorhandene Dienste bleiben an. Achtung: Ältere Staging-Versionen
vor der Zonenfreigabe ignorieren einzelne Zonenzugänge. Nach echten
Berechtigungsänderungen darf deshalb nicht blind auf diese Version zurückgestellt
werden. Im Fehlerfall neue Administration vorübergehend sperren und die
Zonenprüfung beibehalten, bis ein sicherer Stand bereitsteht.


## Kunden bearbeiten, Zonen prüfen und Konten löschen

Unter **Administration → Kunden & Benutzer** zeigt jede Karte die tatsächlich
sichtbaren **Zonennamen**. Die Anzeige nutzt dieselbe serverseitige Zonenprüfung
wie Messungen, Sensoren und Aufzeichnungen. Mitglieder sehen ausschliesslich
freigegebene Zonen ihrer Firma. Eigentümer und Administratoren sehen alle
aktuellen und künftigen Firmenzonen. Deaktivierte oder unbestätigte Konten haben
keinen Zugang; dies wird ausdrücklich angezeigt.

**Bearbeiten** öffnet Name, Telefon, Firma, Rolle, Kontoaktivierung und
Zonenfreigaben. Unter **Firmen verwalten** lässt sich der Firmenname ändern;
Zuordnungen und Messdaten bleiben erhalten.

**Konto löschen** öffnet zunächst eine Bestätigung mit der betroffenen
E-Mail-Adresse. Erst nach erneuter Eingabe dieser Adresse kann **Endgültig
löschen** ausgelöst werden. Dies entfernt das Benutzerkonto und seine
Zonenfreigaben; bestehende Sitzungen verlieren sofort den API-Zugang.
Firmen, Zonen, Sensoren, Messungen und bestehende Audit-/Beobachtungshistorie
bleiben erhalten. Geschützte Administrationskonten, das eigene Konto und der
letzte aktive Firmenverantwortliche können nicht gelöscht werden.

Neue API-Routen: `DELETE /api/v1/administration/users/{id}` mit JSON
`confirmation_email` und `PUT /api/v1/administration/companies/{id}` mit JSON
`name`. Beide erfordern die bestehende zentrale Adminfreigabe. Die Benutzerliste
enthält zusätzlich `visible_zones` und `access_note`. Keine Datenbankmigration.
Production-Abnahme erfolgt lesend; Löschtests laufen nur in isolierten
Testdatenbanken. Die Veröffentlichung selbst löscht keine Kundenkonten.

## Firmen anlegen, löschen und neue Firmenzonen einrichten

Unter **Administration → Firmen verwalten**:

1. **Firma hinzufügen**: Namen eingeben und speichern.
2. **Zone hinzufügen** auf der gewünschten Firmenkarte: Name, optionalen Standort
   und Zonentyp wählen. Die neue Zone gehört unmittelbar dieser Firma.
3. **Benutzer einer Firma zuordnen und Zonen freigeben**: Konto erstellen oder
   bearbeiten, Firma auswählen, Rolle **Mitglied – ausgewählte Zonen** wählen
   und nur die gewünschten Zonen markieren. **Nach Firma filtern** begrenzt
   die Benutzerliste serverseitig, einschliesslich Trefferzahl und Seitenwechsel.
4. Beispiel: Person X gehört Firma Y an. Nur Zone Z markieren, Zone A abwählen.
   X sieht dann Zone Z, auch bei Messungen und Sensoren. Eigentümer und
   Firmenadministratoren behalten dagegen Zugriff auf alle eigenen Firmenzonen.
5. **Firma löschen**: vollständigen Firmennamen zur Bestätigung eingeben.
   Die Löschung ist nur bei einer leeren Firma möglich. Benutzer, Zonen,
   Pflanzen oder andere referenzierende Datensätze blockieren sie mit HTTP 409.
   Es gibt keine automatische kaskadierende Löschung von Konten oder Messdaten.

Bestehende Zonen werden mit dieser Erweiterung nicht zwischen Firmen verschoben.
Ein solcher Transfer muss auch Sensoridentitäten, Direct-Zuordnungen,
Messhistorie und bestehende Freigaben konsistent migrieren. Die bisherigen
Empfangspfade, gespeicherten Zugangsdaten und Hintergrunddienste bleiben unverändert.

Zusätzliche API:

- `POST /api/v1/administration/companies` mit `name`.
- `DELETE /api/v1/administration/companies/{id}` mit `confirmation_name`.
- `POST /api/v1/administration/companies/{id}/zones` mit `name`, optional
  `location`, `zone_type`, `latitude`, `longitude` (bestehendes Zonenschema).
- `GET /api/v1/administration/users?organization_id=<UUID>` für die Firmenfilterung.

Alle Schreibaktionen erfordern die zentrale Adminfreigabe und werden auditiert.
Keine neuen Konfigurationswerte oder Datenbankmigrationen. Veröffentlichung über
oben beschriebenen isolierten Release-Weg nach Prüfung der konkreten Änderung;
kein Neustart der Gateway-/Direct-Empfangsdienste. Frontend und Verwaltungs-API
müssen gemeinsam veröffentlicht werden. Bei Rücknahme bleiben angelegte Firmen,
Zonen und Freigaben bestehen; keine ältere Version ohne Zonenprüfung verwenden.

Lokale Prüfungen: `backend/tests/test_administration.py`,
`backend/tests/test_zone_access.py`, PostgreSQL-Tests in
`backend/tests/test_zone_access_migration.py` sowie Frontend
`Administration.test.tsx`, Typprüfung, Lint und vollständiger Build.
