# Archiv-Abnahme: Zugänge und tatsächlicher Stand

**Aktualisierung 5. Oktober:** [Aktuelle WAV-Metadatenvorbereitung und
Zugangsübersicht](ARCHIVE-WAV-METADATA-20261005.md). Die unten genannten
Wiederherstellungsanforderungen des Vollkatalogs sind durch einen separat
geprüften WAV-Katalog zu ersetzen; sein Export bleibt inaktiv.

Stand 3. Oktober 2026, 17:35 UTC. Keine WAV-Löschung freigegeben.
Keine neuen administrativen Identitäten wurden in dieser Folgearbeit angelegt.
Keine Empfangsdienste, Proxy-Konfigurationen oder bestehenden Kopierzeitpläne
wurden verändert oder neu gestartet.

## Tatsächlich verwendete bestehende Zugänge

| Zugang | Verwendung | Grenzen |
|---|---|---|
| SSH `traver@188.245.247.156` mit vorhandenem Schlüssel | Nur-Lese-Prüfungen und Übertragung der vorbereiteten Prüfprogramme | Kein neuer SSH-Benutzer oder Schlüssel angelegt |
| Bestehendes sudo für `traver` | Private Prüfnachweise lesen; begrenzten, separaten Nur-Lese-Prüfauftrag starten | Keine Credentials aus Empfangs- oder MinIO-Container-Umgebungen gelesen |
| PostgreSQL `greenmind_archive_reader_33fe395` | Eigene Berechtigungen und Katalogzugang prüfen | Live geprüft: keine Superuser-, Rollenanlage-, Datenbankanlage-, Replikations- oder RLS-Umgehungsrechte; acht Verbindungen; 69 Tabellenrechte ausschließlich `SELECT` |
| Bestehende Sicherungskonfiguration `/etc/greenmind/raw-copy/storagebox.env` | Bereits freigegebene S3-/Storage-Box-Verbindungen für Prüfprogramme | Geheimnisse werden weder ausgegeben noch in GitHub, Dokumentation oder öffentliche Reader übernommen |
| Storage Box `u676312@u676312.your-storagebox.de:23` | Bestehendes WAV-Archiv; Ziel der möglichen zusätzlichen Katalogsicherung | SSH ist kein Hetzner-Console/API-Zugang zur Snapshot-Erstellung |

Die vorbereiteten Prüfdienste verwenden keine zusätzlichen administrativen
Cloud-Rollen. Die vorhandenen sudo-Rechte wurden nicht erweitert.

## Noch nicht angelegte oder bereitgestellte Zugänge

| Zugang | Benötigte Funktion | Aktueller Stand |
|---|---|---|
| Regulärer MinIO-Admin-Alias | Einmalig den getrennten Diagnosebenutzer anlegen | Kein ausdrücklich vom Betreiber bereitgestellter Admin-Alias bestätigt. Keine Root-Credential-Suche. `/home/traver/.mc/config.json` existiert; dessen Existenz beweist keinen gültigen Adminzugang. |
| Neuer MinIO-Diagnosebenutzer `greenmind-diagnostic-FILL_ID` | Nur `GetBucketVersioning` und `GetLifecycleConfiguration` auf den beiden exakt benannten WAV-Buckets | Vorbereitet, **nicht angelegt**. Keine Objekt-, Schreib-, Lösch- oder Administrationsrechte. |
| Hetzner-Snapshot-Operatorzugang oder vom Betreiber erstellter Snapshot | Snapshot-Identität und unabhängiges vollständiges Zurücklesen nachweisen | Nicht bereitgestellt. Keine reguläre hcloud-Konfigurationsdatei an den geprüften Benutzerpfaden vorhanden. Keine API-Tokens erzeugt. |

Der installierte `/usr/bin/mc` ist GNU Midnight Commander 4.8.30, kein
MinIO-Client. Die korrigierte Vorbereitung verlangt einen absoluten Pfad zum
echten Client und prüft ihn vor jeder administrativen Aktion. Sie übernimmt
keine Root-Zugangsdaten aus Containern.

Der MinIO-Betreiber muss einen regulären Admin-Alias mit eigenen bekannten
Zugangsdaten bereitstellen. Ein neuer, breit privilegierter Adminbenutzer ist
für die Diagnose nicht nötig; nur der eng begrenzte Diagnosebenutzer soll neu
angelegt werden. Anleitung zum regulären Alias: [MinIO-Client-Dokumentation](https://docs.min.io/aistor/reference/cli/).

Der Betreiber des Hetzner-Kontos kann in Console die Storage Box `u676312`
auswählen, einen Snapshot erstellen und seine Verzeichnisansicht aktivieren.
Alternativ kann er eine private API-Konfiguration mit tatsächlicher Box-ID
bereitstellen. Snapshot-Name, API-Identität und echte Rücklesetests müssen
zusammenpassen; eine reine Verzeichnisansicht ist keine vollständige Abnahme.
[Hetzner-Snapshot-Dokumentation](https://docs.hetzner.com/storage/storage-box/snapshots/).

## Warum der vollständige Katalogexport noch gesperrt bleibt

Der vollständige Wiederherstellungskatalog umfasst auch Benutzer, Zonenrechte,
Sensorzuordnungen und Visualisierungsdaten. Seine aktuelle vollständige Form
enthält E-Mail-Adressen und Passwort-Hashes. Das ist eine zusätzliche
Notfallsicherung des Systems; für das Kopieren der WAV-Dateien werden diese
Anmeldedaten **nicht benötigt**. Die bereits vorhandene WAV-Kopierung ist
unabhängig und läuft weiter.

Die automatische Freigabeprüfung hat die Aktivierung dieses vollständigen
Exports abgewiesen: allgemeine Zustimmung genügt dort nicht als ausdrückliche
Erlaubnis des konkreten sensiblen Inhalts und des Empfängerkontos `u676312`.
Der Nutzer fragte anschließend nach dessen Zweck, gab diese genaue Freigabe
aber noch nicht. Der Auftrag wurde deshalb **nicht aktiviert**; sein Timer
wurde nachweislich nicht installiert. Die zusätzliche Sicherung darf nicht
als Voraussetzung für gewöhnliche WAV-Kopien dargestellt werden. Eine
zukünftige getrennte Archiv-Metadatensicherung ohne Konten-Geheimnisse wäre
ein anderer Sicherungsumfang und müsste eigenständig restaurierbar geprüft
werden; sie ist derzeit nicht implementiert oder als vollständiger Restore
anerkannt.

## Prüfungen dieser Folgearbeit

- 16 gezielte Tests bestanden: Aktivierungsvorlagen, Ziel-/Löschungsflag-Sperren,
  Client-Verwechslung, Fristablauf, Ressourcenpause, Diagnosefehler, Beobachtung
  und Snapshot-Rücklesen. Formatierung, Lint und Shell-Syntax bestanden.
- 17:28 UTC: elf Beobachtungsproben, keine Fehler, keine Gesundheitsausfälle,
  keine Veränderungen der geschützten Dienste; Fortschritt beider Aufnahmewege
  beobachtet. 24 Stunden sind noch nicht vollständig abgenommen.
- 17:34 UTC: zusätzlicher Nur-Lese-WAV-Prüfauftrag mit 128 MiB RAM, 10 % CPU,
  keinem Swap und 90 Sekunden maximaler Laufzeit. Der Speicherschutz pausierte
  **vor** Rückleseoperationen: 486,6 MiB verfügbar bei 512 MiB Mindestreserve.
  Kein WAV-Rückleseerfolg und kein Snapshot-Nachweis werden dafür behauptet.
- Private Testnachweise bleiben unter
  `/mnt/HC_Volume_106755700/greenmind-delete-preparation/d344695ef260/reports/`
  erhalten. Keine Originale oder Testnachweise entfernt.

## Rücknahme

Zum Aussetzen der bestehenden neuen Beobachtung ausschließlich
`greenmind-archive-observation-d344695ef260.timer` stoppen. Private Nachweise
behalten. Der inaktive Reader-Kandidat bleibt vorbereitet; Reader 8140,
Frontend, alte statische Fallbacks, Empfangscontainer und alte Zeitpläne bleiben
erhalten. Es gibt derzeit keinen aktiven neuen Katalog-Sicherungstimer.

Eine Löschung ist weiterhin ausgeschlossen. Echte Snapshot-, Restore-,
Download-, vollständige Rückstands- und 24-Stunden-Nachweise sowie eine
separate menschliche Pilotfreigabe bleiben erforderlich.
