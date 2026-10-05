# Betreiberzugänge und Reihenfolge vor einer WAV-Löschfreigabe

Stand: 5. Oktober 2026. Keine Löschfreigabe. Ein bestandener 24-Stunden-Test
allein reicht nicht; sämtliche folgenden Nachweise müssen tatsächlich vorliegen.

## Was MinIO ist und welcher Zugang benötigt wird

MinIO ist der bereits auf dem GreenMind-Hetzner-Server laufende S3-kompatible
Objektspeicher. Die WAVs liegen dort in `greenmind-raw` und
`greenmind-direct-production-hotspot`. Es wird kein Konto bei einem neuen
Cloud-Anbieter benötigt. PostgreSQL hält den Dashboard-/WAV-Katalog und die
wissenschaftlichen Features; die Storage Box ist das ausgelagerte RAW-Archiv.

SSH als `traver` mit sudo ermöglicht die serverseitige Vorbereitung. Für
MinIO-Benutzerverwaltung ist zusätzlich eine reguläre MinIO-Betreiber-Anmeldung
erforderlich. Das SSH-Passwort ist kein MinIO-Passwort. Die geprüfte Konfiguration
`/home/traver/.mc/config.json` hat am 5. Oktober keinen nutzbaren lokalen Alias:
`local` zeigt auf Port 9000, seine Credential-Felder sind leer. Fremde Beispiel-
Aliasse sind kein Zugang zu GreenMind.

Benötigt wird einmalig ein vom Betreiber regulär eingerichteter Alias für
`http://127.0.0.1:9000` mit bereits bekannten administrativen Zugangsdaten.
Diese Daten gehören in eine private Konfiguration, nicht in Chat, GitHub,
öffentliche Container oder Nachweisdateien. Keine Root-Geheimnisse aus
Container-Umgebungen auslesen. Keine unbekannte Anmeldung zurücksetzen:
laufende Empfangsdienste könnten diese Zugangsdaten verwenden.

Ein neuer allgemeiner MinIO-Adminbenutzer ist nicht erforderlich. Der Betreiber
legt nur die getrennte Diagnoseidentität an. Ihre neue zufällige Secret-Key-
Zeichenfolge dient als Maschinenpasswort; sie ist unabhängig von Dashboard-
Benutzern. Die einzige Richtlinie erlaubt `s3:GetBucketVersioning` und
`s3:GetLifecycleConfiguration` für exakt die beiden WAV-Buckets. Keine Objekt-,
Upload-, Lösch- oder Administrationsrechte. Dafür liegt
`deploy/raw-archive/delete-preparation/diagnostic-policy.production.json` vor.

Das vorhandene `provision-diagnostic.sh` verlangt einen absoluten, überprüften
MinIO-Clientpfad und einen ausdrücklich eingerichteten Alias. `/usr/bin/mc`
auf diesem Server ist Midnight Commander; diesen Pfad niemals verwenden.
Ein echter Client ist separat bereitgestellt:
`/home/traver/greenmind-operator-preparation-20261005-1055/mc`,
Version `RELEASE.2025-08-13T08-35-41Z`, SHA-256
`01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891`.
Hersteller-GitHub-Asset-Digest und übertragenes Binary wurden verglichen;
`--version` wurde ausgeführt. Kein vorhandenes Programm wurde ersetzt und
keine Anmeldung oder Benutzeranlage damit durchgeführt. Dieser Pfad kann
als `GREENMIND_MINIO_CLIENT` verwendet werden.
Die administrativen CLI-Aufrufe müssen ausschließlich in einer privaten
Betreibersitzung ohne Shell-Tracing laufen. Secrets nicht als ausgeschriebene
Befehle in die Shell-History schreiben. Nach Einrichtung nur die eng begrenzte
Diagnosekonfiguration an die privaten Prüfdienste geben, keine Admin-Anmeldung.

Um die bestehende MinIO-Console vom Mac privat zu öffnen:

```sh
ssh -N -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes -L 127.0.0.1:19001:127.0.0.1:9001 traver@188.245.247.156
```

Danach im Browser `http://127.0.0.1:19001` öffnen. Der Tunnel schafft keine
MinIO-Berechtigung: Ein regulärer Betreiber muss sich mit seinen vorhandenen
MinIO-Zugangsdaten anmelden. Das Terminal bleibt währenddessen geöffnet.
Es wird kein Port öffentlich freigegeben und kein Container neu gestartet.

MinIO-Dokumentation: [Benutzer anlegen](https://minio.community/community/minio-object-store/reference/minio-mc-admin/mc-admin-user-add.html),
[Richtlinie zuordnen](https://minio.community/community/minio-object-store/reference/minio-mc-admin/mc-admin-policy-attach.html).

## Was Peter als Hetzner-Kontobetreiber erledigen muss

Der Storage-Box-SSH-Schlüssel ermöglicht Dateioperationen, aber keine
providerseitige Anlage von Snapshots oder Unterkonten. Dafür werden die
Betreiberrechte des Hetzner-Projekts benötigt. Peter kann diese Schritte selbst
erledigen; seine persönlichen Hetzner-Login-Daten müssen nicht weitergegeben werden.

1. Storage Box `u676312` in Hetzner Console öffnen. Die numerische Box-ID
   festhalten; sie ist nicht automatisch identisch mit `676312`.
2. Nach vollständigem Metadatenexport und Abgleich einen manuellen Snapshot
   erstellen. Snapshot-Name, numerische Snapshot-ID und Erstellungszeit melden.
   Snapshot-Verzeichnis sichtbar machen. Keine Rücksetzung der laufenden Box:
   geprüft wird durch Herunterladen aus dem Snapshot in ein getrenntes Ziel.
3. Einen Unteraccount mit Zugriff auf `greenmind-raw` erstellen und
   **schreibgeschützt** setzen. Benutzername, Hostname, Basisverzeichnis und
   aktivierten SSH-Port melden. Einen eigenen SSH-Schlüssel verwenden. Der
   öffentliche Dashboard-Reader erhält keine Upload-Zugangsdaten.
4. Für die unabhängige Bestätigung der Box-/Snapshot-Identität einen
   **Read-only API-Token** im tatsächlichen Projekt der Storage Box erstellen
   und als private Datei bereitstellen, beispielsweise
   `/etc/greenmind/delete-preparation/hetzner-token`, Modus 0600. Nur den
   Dateipfad mitteilen. Ein Read-only Token kann den von Peter erstellten
   Snapshot prüfen; er kann selbst keine Snapshots erstellen.

Ein schreibfähiger Projekt-Token wäre für automatisches Erstellen erforderlich
und könnte weitere Ressourcen verändern. Für diese Abnahme ist ein von Peter
erstellter manueller Snapshot plus Read-only Token vorzuziehen. API-Zugriff
ersetzt keine vollständigen Snapshot-Rücklesetests.

Hetzner-Dokumentation: [Snapshots](https://docs.hetzner.com/storage/storage-box/snapshots/),
[Unterkonten und schreibgeschützter Zugriff](https://docs.hetzner.com/storage/storage-box/general/),
[API-Token](https://docs.hetzner.com/cloud/api/getting-started/generating-api-token/).
Snapshots bleiben auf derselben Storage Box und sind keine unabhängige
Sicherung gegen Verlust dieses Kontos/Providers.

## Welche Passwörter ausdrücklich nicht benötigt werden

Keine Dashboard-Benutzerpasswörter oder Passwort-Hashes, keine allgemeine
Benutzerdatenbank auf der Storage Box, kein Passwort von Peters persönlichem
Hetzner-Konto, keine neuen Raspberry-Pi- oder Sensor-Anmeldedaten. Der WAV-only
Katalog enthält lediglich freigegebene Archiv-/Sensor-/Zonen-IDs,
wissenschaftliche Features, Signalparameter und Visualisierungsaggregate.
Dashboard-Berechtigungen bleiben im bestehenden authentifizierten Backend.

## Tatsächliche Reihenfolge zur Produktionsabnahme

| Schritt | Nachweis | Konsequenz bei fehlendem Nachweis |
|---|---|---|
| 1. Betreiberzugänge | Regulärer MinIO-Alias, enges Diagnosekonto, Read-only Box-Unterkonto, Snapshot-Operator/Read-only API | Keine erfundene Freigabe; keine Geheimnissuche |
| 2. Bucket-Zustand | Tatsächliche Versionierung und Lifecycle-Antworten beider Buckets | `AccessDenied` ist unbekannter Zustand; nichts löschen |
| 3. WAV-Metadaten | Vollständiger erlaubter Export, alle Teile veröffentlicht und vollständig rückgelesen | Unvollständige Exporte privat behalten, kein Restore-PASS |
| 4. Wiederherstellung | Leere separate Archivdatenbank, Hash-/Zeilen-/Tabellenprüfung | Live-PostgreSQL nicht verändern |
| 5. Bestand | Fester Inventarstand, alle berechtigten Dateien abgeglichen, Quarantäne erhalten | Kein Teilabgleich als vollständiger Backlog-Nachweis |
| 6. Provider-Snapshot | Provider-ID bestätigt; Pilot-WAVs und kompletter Metadatenkatalog aus Snapshot restauriert | Verzeichniszugriff allein genügt nicht |
| 7. Reader-Kandidat | Original/Archiv-URLs, reale Zonen-/Jobrechte, ZIP/SHA/WAV, CSV-Anzeige 1h/7d/30d/1y, Fortschritt unter Speichergrenze | Kandidat nicht produktiv umschalten |
| 8. Proxy | Minimaler hash-gebundener Wechsel, Rücknahme und Aufnahmewege geprüft | Originale und alte Routen behalten |
| 9. Frische Beobachtung | Mindestens 24h nach akzeptierter Veröffentlichung, Gateway und Direct fortschreitend | Frühere Beobachtung akzeptiert neue Veröffentlichung nicht |
| 10. Menschlicher Pilot | Frische prüfsummengebundene konkrete Freigabe, maximal zehn Dateien/fünf MiB | Löschung bleibt deaktiviert |

Die lokale Sieben-Tage-Reserve wird nicht übersprungen. Ein erfolgreicher Pilot
ist keine automatische Freigabe für unbegrenzte Löschung. Snapshots und Metadaten
müssen alle konkret freigegebenen Dateien abdecken. Versionierung darf nicht
nebenbei eingeschaltet oder Lifecycle verändert werden; eine nötige Änderung
braucht einen gesondert geprüften Betriebsplan.

## Ressourcen und bestehender Betrieb

Am 5. Oktober um 12:46 Schweizer Zeit: rund 535 MiB verfügbarer RAM und
beträchtliche Swap-Belegung. Der Metadatenprüfpfad benötigt 640 MiB freie
Hostreserve; der neue Reader startet erst bei mindestens 704 MiB. Die Grenzen
bleiben bestehen. Keine vorhandenen Empfangsdienste oder Overlays werden für
eine Abnahme gestoppt. Bei dauerhaft zu wenig Reserve muss ein isolierter
Prüfhost oder zusätzliche Kapazität eingeplant werden. Eine große vollständige
Wiederherstellung darf nicht unbemerkt den Produktionshost belasten.

Anleitung für private Downloads: [Storage Box herunterladen](STORAGE-BOX-DOWNLOAD.md).
