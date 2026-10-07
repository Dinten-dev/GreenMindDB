# WAV-Archiv: Betreiberanleitung und Freigabeschritte

Stand: 7. Oktober 2026. Löschung bleibt deaktiviert.

Aktualisiert nach der Betreiber-Anmeldung: Diagnosekonto angelegt und beide
Buckets tatsächlich geprüft. Zwei WAV-Downloads über den Unteraccount bestanden.
Versionierung ist in **beiden** Produktions-Buckets nicht aktiviert.

## Aktueller Zustand

| Schritt | Tatsächlicher Nachweis | Offen |
|---|---|---|
| MinIO-Diagnose | Enges Diagnosekonto angelegt; tatsächliche Richtlinie geprüft | Kein weiterer Betreiber-Login erforderlich |
| Bucket-Zustand | Beide unversioniert; keine Lifecycle-Regeln; echte Objektzugriffe verweigert | Geprüfter Versionierungsplan und Legacy-null-Abnahme |
| WAV-only-Katalog | Unveränderliches Paket installiert; Export geschützt versucht | Letzter Versuch: 506,6 MiB; benötigt 640 MiB |
| Fester Bestand | Abgleich und Quarantänelogik vorbereitet | Vollständiger neuer Katalog und echter Abgleich |
| Snapshot | Vorläufiger Snapshot bestätigt; zwei WAV-Rücklesungen bestanden | Abschließender Snapshot nach Katalogveröffentlichung |
| Lesezugang | Gateway-/Direct-WAV über Unteraccount: SHA und Dekodierung bestanden | Dienst-Aktivierung und vollständige Kandidatentests fehlen |
| Proxy | Aktueller Entwurf; Rücknahme lokal bytegenau geprüft | Nginx-Probe, echter Wechsel und öffentliche Abnahme |
| 24 Stunden | Frühere Beobachtung läuft separat | Frischer Test nach akzeptierter Veröffentlichung |

Gateway 8120, Direct 8003, Legacy 8000: HTTP 200.
Empfänger wurden nicht neugestartet oder verändert.
Der Reader 8141 läuft noch nicht.
Der aktive Reader 8140 bleibt erhalten.

## 1. MinIO-Zugang auf dem Hetzner-Server

Für diese Abnahme bereits abgeschlossen. Nicht erneut provisionieren.
Betreiber-Alias: `/home/traver/.config/greenmind/minio-operator-a3b88514f95551f1`.
Neue Diagnoseidentität: `greenmind-diagnostic-20261007-48d6f68755ae94ac`.
Private Konfiguration:
`/home/traver/greenmind-operator-access-20261007/greenmind-diagnostic-20261007-48d6f68755ae94ac/diagnostic.env`.
Die folgenden Einrichtungsschritte bleiben eine Anleitung für neue Betreiber.

MinIO läuft bereits auf `188.245.247.156`.
Kein neues MinIO-Cloud-Konto wird benötigt.
Hetzner-, SSH- und MinIO-Anmeldungen sind voneinander unabhängig.

Der erste MinIO-Administrator wurde bei der Servereinrichtung konfiguriert.
Peter beziehungsweise der ursprüngliche Betreiber muss dessen bekannte
Anmeldung aus seiner regulären Passwortverwaltung bereitstellen oder den
folgenden Schritt selbst durchführen. Ohne bestehenden Administrator lässt
sich kein Diagnosekonto regulär anlegen. Nicht raten, keine Container-Geheimnisse
auslesen und kein unbekanntes Passwort zurücksetzen.

### Bestehende Console privat vom Mac öffnen

```sh
ssh -N -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes -L 127.0.0.1:19001:127.0.0.1:9001 traver@188.245.247.156
```

Browser: `http://127.0.0.1:19001`.
Mit dem vorhandenen MinIO-Login anmelden.
Das Tunnel-Terminal geöffnet lassen.
Nicht das Hetzner- oder SSH-Passwort ausprobieren.

### Regulären Betreiber-Alias einrichten

Nach der SSH-Anmeldung bei `traver@Biolingo` ausschließlich die folgende
Befehlszeile kopieren, keine Markdown-Zeichen und keinen Befehlsblock mit `read`.
Die Einrichtungshilfe fragt die bekannte Anmeldung interaktiv ab, verdeckt
das Passwort und speichert einen neuen privaten Alias. Bestehende Aliasse
bleiben unverändert. Der Client liest Zugangsdaten aus der privaten Datei;
sie werden nicht als Prozessargumente oder Ausgaben übergeben.

```sh
python3 /home/traver/greenmind-operator-access-20261007/configure-operator.py
```

Nur bekannte Betreiber-Zugangsdaten verwenden.
`/usr/bin/mc` ist hier Midnight Commander, kein MinIO-Client.
Bei fehlgeschlagener Anmeldung stoppen; keine Passwortänderung durchführen.
Die erfolgreiche Ausgabe enthält den neuen Konfigurationsverzeichnis-Pfad.
Nur diesen Pfad und die PASS-Meldung mitteilen, keine Zugangsdaten.

### Metadaten-Diagnosekonto anlegen

Nur nach erfolgreicher Alias-Einrichtung fortfahren.
Die neue Identität erhält ausschließlich zwei Bucket-Leserechte.
Keine Objekt-, Upload-, Lösch- oder Administrationsrechte.
Die Vorbereitung verweigert bestehende Konten/Richtlinien und unklare Antworten.
Bei einem Teilfehler private Nachweise behalten; nichts automatisch entfernen.

```sh
bash
export MC_CONFIG_DIR='/home/traver/.config/greenmind/minio-operator-GEMELDETE_KENNUNG'
export GREENMIND_MINIO_CLIENT=/home/traver/greenmind-operator-preparation-20261005-1055/mc
export GREENMIND_DIAGNOSTIC_DIRECT_BUCKET=greenmind-direct-production-hotspot
export GREENMIND_DIAGNOSTIC_ACCESS="greenmind-diagnostic-20261007-$(openssl rand -hex 8)"
export GREENMIND_DIAGNOSTIC_SECRET="$(openssl rand -hex 32)"
bash /home/traver/greenmind-operator-access-20261007/provision-diagnostic.sh greenmind-operator /home/traver/greenmind-operator-access-20261007/diagnostic-policy.production.json
```

Nur bei vollständig erfolgreicher Provisionierung die Diagnosekonfiguration
in derselben privaten Sitzung speichern. Ein neues Verzeichnis verwenden:

```sh
GREENMIND_DIAGNOSTIC_DIR="$HOME/.config/greenmind/$GREENMIND_DIAGNOSTIC_ACCESS"
mkdir -m 700 "$GREENMIND_DIAGNOSTIC_DIR"
printf 'RAW_ARCHIVE_DIAGNOSTIC_ENDPOINT=http://127.0.0.1:9000\nRAW_ARCHIVE_DIAGNOSTIC_ACCESS_KEY=%s\nRAW_ARCHIVE_DIAGNOSTIC_SECRET_KEY=%s\nRAW_ARCHIVE_DIRECT_S3_BUCKET=greenmind-direct-production-hotspot\n' "$GREENMIND_DIAGNOSTIC_ACCESS" "$GREENMIND_DIAGNOSTIC_SECRET" > "$GREENMIND_DIAGNOSTIC_DIR/diagnostic.env"
chmod 600 "$GREENMIND_DIAGNOSTIC_DIR/diagnostic.env"
unset GREENMIND_DIAGNOSTIC_SECRET
```

Danach nur den **Konfigurationspfad** mitteilen.
Keine Datei-Inhalte oder Passwörter senden.
Der Betreiber-Alias wird nicht in öffentliche Container eingebunden.

Referenz: [MinIO-Benutzerverwaltung](https://min.io/docs/minio/macos/administration/identity-access-management/minio-user-management.html).

## 2. Wirkliche Bucket-Versionierung und Lifecycle prüfen

Tatsächliche Abfragen am 7. Oktober, 16:34 Schweizer Zeit, abgeschlossen:
Beide Antworten `versioning={}`, jeweils `NoSuchLifecycleConfiguration`.
Das bestätigt **keine Versionierung und keine Lifecycle-Richtlinie**.
Die Metadatenabfrage besteht; die Löschvoraussetzung `Enabled` besteht nicht.
Bei späterer Aktivierung bleiben bestehende Objekte zunächst null-versioniert.
Versionierung und Legacy-null-Verhalten separat prüfen; nichts blind aktivieren.
[S3-Protokollreferenz](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Versioning.html).

Exakte Buckets:

- `greenmind-raw`
- `greenmind-direct-production-hotspot`

Die Diagnosekonfiguration in derselben Bash-Sitzung laden.
Sie enthält ausschließlich die neue Metadatenidentität.

```sh
set -a
. "$GREENMIND_DIAGNOSTIC_DIR/diagnostic.env"
set +a
PYTHONPATH=/opt/greenmind/wav-metadata/371dfe70b998/backend /opt/greenmind/raw-copy/20260925/venv/bin/python -m app.raw_archive.diagnostics > "$GREENMIND_DIAGNOSTIC_DIR/bucket-state.json"
chmod 600 "$GREENMIND_DIAGNOSTIC_DIR/bucket-state.json"
unset RAW_ARCHIVE_DIAGNOSTIC_SECRET_KEY
```

`AccessDenied` bedeutet **unbekannt**, niemals „deaktiviert“.
`NoSuchLifecycleConfiguration` bezeichnet keine konfigurierte Lifecycle-Regel.
Versionierung muss tatsächlich `Enabled` sein, bevor entsprechende
Löschbedingungen als erfüllt gelten. Fehlende Versionierung oder aktive
Verfallsregeln erfordern einen gesonderten geprüften Betriebsplan. Hier nichts
einschalten, ändern oder entfernen.

## 3. WAV-only-Katalog vollständig sichern und restaurieren

Vorbereiteter manueller Runner:
`/home/traver/greenmind-operator-access-20261007/run-wav-gate.py`.

- Keine automatische Aktivierung oder Zeitplanung.
- Alle Lösch- und Pruning-Flags bleiben false.
- Mindestens 640 MiB verfügbarer Host-RAM.
- Hostlast höchstens 2,4.
- Geschützte Empfangsrouten müssen gesund sein.
- Worker: 128 MiB RAM, kein Swap, 10 % CPU.
- Export/Restore: zwei Minuten innere Laufzeitgrenze.
- Teilresultate bleiben privat erhalten.

### Einmaliger Exportversuch

```sh
sudo python3 /home/traver/greenmind-operator-access-20261007/run-wav-gate.py export
```

Bei `paused` wurde keine neue Arbeit gestartet.
Bei `worker_returncode=0` zusätzlich das private Ergebnis prüfen:
Nur ein vollständiges Manifest zählt, keine vorhandenen Teil-Dateien.
Keine Benutzerkonten, E-Mails oder Passwort-Hashes werden exportiert.

Der Runner meldet den neuen privaten Ausgabeordner.
`CATALOG_DIR` nachfolgend durch genau diesen erfolgreichen Ordner ersetzen:

```sh
CATALOG_DIR='/mnt/HC_Volume_106755700/greenmind-wav-metadata/371dfe70b998/operator-export-ERFOLGREICHER_LAUF'
sudo python3 /home/traver/greenmind-operator-access-20261007/run-wav-gate.py publish --manifest "$CATALOG_DIR/manifest.json"
```

Das erfolgreiche `publication-*.json` enthält `offsite_manifest`:
`key`, `sha256`, `size`. Diese drei Werte als private `reference.json`
im Katalogordner speichern, dem Konto `greenmind-raw-copy` lesbar machen.
Keine Referenz auf unvollständig veröffentlichte Teile erzeugen.

```sh
sudo python3 /home/traver/greenmind-operator-access-20261007/run-wav-gate.py restore --reference "$CATALOG_DIR/reference.json"
```

Der Restore erzeugt eine **neue, getrennte SQLite-Archivdatenbank**.
Keine Tabellen der laufenden PostgreSQL werden ersetzt.
Alle Teile, SHA-256, Zeilen-/Tabellenzahlen und Datenbankintegrität prüfen.

Bei dauerhaft fehlender Reserve oder Zeitüberschreitung einen isolierten
Prüfhost einsetzen. Geschützte Empfänger nicht stoppen und Grenzen nicht
senken. Dort werden vollständige veröffentlichte Katalogteile über Leserechte
geladen und mit `wav_catalog.restore_bundle` in einer neuen Archivdatenbank
verifiziert. Der Produktionszugriff bleibt zeitlich und mengenmäßig begrenzt;
keine Upload- oder MinIO-Admin-Geheimnisse auf diesen Prüfhost kopieren.
Dieser isolierte Pfad ist noch nicht als durchgeführter Restore bestätigt.

## 4. Festen Bestand abgleichen und Quarantäne behalten

Das vollständige Exportmanifest definiert den festen Inventarstand.
Danach entstandene Dateien gehören in einen späteren Bestand.
Den vorhandenen geprüften Quarantäneregister-Pfad verwenden.
Keine historische Problemzeile oder WAV entfernen.

```sh
QUARANTINE='/mnt/HC_Volume_106755700/greenmind-wav-metadata/371dfe70b998/GEPRUEFTES_QUARANTAENEREGISTER.json'
sudo python3 /home/traver/greenmind-operator-access-20261007/run-wav-gate.py reconcile --manifest "$CATALOG_DIR/manifest.json" --quarantine "$QUARANTINE"
```

`eligible_pending_files`, `receipt_mismatches`, unbekannte berechtigte Dateien:
vollständig erklären beziehungsweise auf null bringen.
Quarantäne separat ausweisen und weiterhin von Löschung ausschließen.
Ein passender Journalbeleg ersetzt keine physische Rücklesung.
Die lokale Sieben-Tage-Reserve bleibt zusätzlich verpflichtend.

## 5. Abschließenden Snapshot erstellen und unabhängig rücklesen

Der vorhandene Snapshot ist nur ein vorläufiger Schutzpunkt:

- Box-ID: `658321`.
- Snapshot-ID: `1414399`.
- Verzeichnisname: `2026-10-07T12-45-05`.
- Zwei WAV-Proben erfolgreich; kein vollständiger Katalog-Restore.

**Erst nach vollständiger Katalogveröffentlichung und Abgleich**:

1. Hetzner Console → Storage Boxes → `storage-box-1`.
2. Snapshots → manuellen Snapshot erstellen.
3. Eindeutigen Namen mit Datum und Katalogkennung verwenden.
4. Snapshot-ID, Verzeichnisname und Erstellungszeit festhalten.
5. GET-only-API-Prüfung bestätigt Box und Snapshot unabhängig.
6. Kompletten Katalog aus diesem Snapshot separat restaurieren.
7. Alle später konkret freigegebenen Pilot-WAVs vollständig rücklesen.

Keinesfalls „Storage Box wiederherstellen“ anklicken: Das wäre eine
Rücksetzung des laufenden Archivs. Der Test liest ausschließlich aus dem
Snapshot in ein neues privates Ziel.

```sh
FINAL_SNAPSHOT='TATSAECHLICHER_NEUER_SNAPSHOT_VERZEICHNISNAME'
sudo python3 /home/traver/greenmind-operator-access-20261007/run-wav-gate.py restore --reference "$CATALOG_DIR/reference.json" --snapshot "$FINAL_SNAPSHOT"
```

Der vorhandene API-Token bleibt ausschließlich auf dem Mac:
`~/.config/greenmind/hetzner-readonly-token`, Modus 0600.
Ein Read-only-Token erstellt keine Snapshots.
Der WAV-Unteraccount bleibt auf `greenmind-raw` beschränkt;
Snapshot-Rücklesung erfolgt getrennt über den bereits erlaubten Operatorpfad.

Referenz: [Hetzner-Snapshots](https://docs.hetzner.com/storage/storage-box/snapshots/).

## 6. Unteraccount und Reader-Kandidat abnehmen

Vorhandener Unteraccount: `u676312-sub1`, schreibgeschützt.
Host: `u676312-sub1.your-storagebox.de`, SSH-Port 23.
Virtuelles Basisverzeichnis entspricht bereits `greenmind-raw`.

Dienst-Konfiguration vorbereitet, **noch nicht aktiviert**:
`/etc/greenmind/archive-readonly-20261007/broker.env`.
Der separate Schlüssel liegt im selben privaten Verzeichnis.
Eigentümer root; Leserechte nur Gruppe `greenmind-raw-copy`.
Keine Upload-Schlüssel oder Betreiber-Aliasse öffentlich einbinden.

Vor Kandidatenstart mindestens 704 MiB freie Hostreserve sicherstellen.
Danach alle folgenden realen Prüfungen durchführen:

- Gateway- und Direct-WAV vollständig rücklesen.
- Original- und Archiv-URLs authentifiziert vergleichen.
- Nicht erlaubte Zonen tatsächlich verweigern.
- Export-Jobs zwischen echten Benutzern isolieren.
- ZIP-Manifeste und jeden WAV-SHA-256 prüfen.
- WAV-Dateien vollständig dekodieren.
- CSV gegen angezeigte Werte vergleichen.
- Zeiträume: 1 Stunde, 7 Tage, 30 Tage, 1 Jahr.
- Unter 512-MiB-Downloadschutz echten Fortschritt nachweisen.
- Kandidat darf keine Aufnahme-Endpunkte anbieten.

Für Archivtests keine Originale löschen. Stattdessen ausschließlich im
isolierten Kandidaten einen getesteten lokalen Quellenadapter verwenden,
der für die exakt freigegebenen Proben „nicht vorhanden“ liefert.
Die reale Storage Box wird dabei tatsächlich gelesen.

## 7. Minimalen Reader-Proxy wechseln und Rücknahme prüfen

Der alte vorbereitete Reader-Proxy ist überholt.
Sein Basishash stammt vor den heutigen Frontend-Änderungen.
Nicht dessen Aktivierungsskript ungeprüft ausführen.

Neuer privater Entwurf:
`/home/traver/greenmind-operator-access-20261007/candidate.conf`.
Basishash:
`9dd6e1e8741493c973c1cccf6932278604e7240e44604babb4532e53b28838cc`.

Vor Veröffentlichung neuen unveränderlichen Reader-Release vorbereiten:

1. Aktuellen Proxy erneut lesen und Hash vergleichen.
2. Nur geprüfte WAV-Leserouten als Overlay ergänzen.
3. Englisches Frontend und sämtliche bisherigen Overlays erhalten.
4. Nginx-Syntax vor Veröffentlichung prüfen.
5. Frischen echten Kandidaten-Nachweis prüfsummengebunden übernehmen.
6. Probewechsel und bytegenaue Rücknahme durchführen.
7. Empfangergesundheit, Startzeiten und Neustartzähler vergleichen.
8. Danach veröffentlichen und öffentliche Downloads erneut prüfen.

Der neue Entwurf ist **keine aktivierte oder live erprobte Konfiguration**.
Keinen generischen Release-Proxy-Generator verwenden.
Bei Hashabweichung, fehlender Autorisierung oder fehlerhaften Downloads stoppen.
Nur den neuen Reader-Overlay zurücknehmen; Empfangsrouten erhalten.

## 8. Frische 24-Stunden-Abnahme und separate Pilotfreigabe

Die Beobachtung startet erst **nach** akzeptierter Veröffentlichung.
Release- und Proxy-Hash gehören in die neue private Baseline.
Frühere Beobachtung zählt nicht für den neuen Release.

- Mindestens 24 Stunden ab Veröffentlichungsabnahme.
- Regelmäßige Proben ohne große Beobachtungslücken.
- Gateway und Direct müssen neue Daten aufnehmen.
- Empfangergesundheit und Ressourcenreserve nachweisen.
- Downloads müssen tatsächlich erfolgreich fortschreiten.
- Keine Datenlücken oder unerklärten Archivabweichungen.
- Inventar und Snapshot müssen Pilotdateien vollständig abdecken.

Danach eine **neue ausdrückliche menschliche Pilotfreigabe** einholen.
Sie benennt konkrete Dateien und deren SHA-256.
Höchstens zehn Dateien beziehungsweise fünf MiB.
Freigabemanifest maximal 24 Stunden gültig.
Vor jeder späteren Löschung erneut echte Bytes prüfen.
Ein Pilot erlaubt keine unbegrenzte Löschung.

## Zugangsdokumentation

| Identität | Zweck | Stand |
|---|---|---|
| `traver` + bestehender SSH-Key | Private Vorbereitung und freigegebenes sudo | Verwendet; kein neuer OS-Account |
| `u676312` | Bestehender Betreiber-/Kopierzugang | Unverändert; nicht im Reader montiert |
| `u676312-sub1` | Archiv ausschließlich lesen | Provider bestätigt; separates Dienst-Key vorbereitet |
| Neuer SSH-Key | Maschine liest den Unteraccount | Fingerprint `SHA256:8fDimijrgNM6iuxc5c9EyMFv19LRaXZnkOYwgQwecqw` |
| Hetzner-Read-Token | Provider-Identität prüfen | Privat auf Mac; keine Schreibprüfung |
| MinIO-Betreiber-Alias | Enges Diagnosekonto erstellen | Vom Betreiber eingerichtet; auf Server verwendet |
| MinIO-Diagnoseidentität | Versionierung/Lifecycle lesen | Angelegt; zwei Buckets tatsächlich geprüft |

Keine Löschrichtlinie wurde aktiviert. Keine WAV wurde gelöscht.
