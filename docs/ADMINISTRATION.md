# Administration: Speicher und Benutzer

Entwicklungsumgebung: `develop` → `https://test.green-mind.ch` (Staging).
Production wird durch diese Veröffentlichung nicht geändert.

## Bedienung

Freigegebene Konten sehen unter **Verwaltung → Administration** den Serverspeicher
und die neue Kachel **Benutzerverwaltung**. Die vorhandenen Verwaltungseinträge
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
dem Administrator. Keine automatische Passwort-Rücksetzung oder Benutzerlöschung.

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
