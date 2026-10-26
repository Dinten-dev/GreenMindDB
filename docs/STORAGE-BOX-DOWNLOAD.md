# Alle derzeit archivierten GreenMind-Daten auf den Mac herunterladen

Quelle: `u676312@u676312.your-storagebox.de`, SSH-Port 23.
Der bestehende Mac-Schlüssel `~/.ssh/id_ed25519` wurde am 5. Oktober für zwei
WAV-Rücklesetests erfolgreich verwendet. Kein weiteres Passwort nötig.

Die Schritte sind eine Anleitung: Ein vollständiger Massendownload wurde
damit noch nicht ausgeführt. Es wird ausschließlich von der Storage Box zum
Mac übertragen, nicht über den produktiven GreenMind-Server. Netzwerk/Disks
der Storage Box sind trotzdem geteilte Ressourcen; eine Verbindung und
anfänglich 5 MiB/s Bandbreitenbegrenzung lassen Reserve für den Kopierdienst.

## Ziel mit genügend Speicher wählen

Im Terminal des Macs starten. Für große Bestände eine externe SSD mit genügend
freiem Speicher verwenden; den beispielhaften Volumennamen ersetzen.
`du` kann bei vielen Dateien länger benötigen: bis zum Ergebnis warten, keinen
zweiten gleichzeitigen Scan starten. Ein unbekannter SSH-Fingerprint muss
mit der offiziellen Hetzner-Liste verglichen werden, nicht blind akzeptiert.

```sh
ssh -p 23 -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes u676312@u676312.your-storagebox.de du -sk greenmind-raw
df -h /Volumes/DEINE-SSD
umask 077
mkdir -p /Volumes/DEINE-SSD/GreenMind-StorageBox/greenmind-raw
```

Der erste Befehl misst den archivierten GreenMind-Verzeichnisbaum, nicht den
gesamten Originalbestand auf Hetzner. Noch nicht archivierte WAVs sind kein
Bestandteil dieses Downloads. Mindestens die ausgegebene KiB-Menge plus
Reserve auf dem Ziel vorhalten.

## Archiv inklusive aller WAV-Tage, Sensoren und Archivmetadaten kopieren

Dieser Aufruf ist mit dem auf diesem Mac vorhandenen `rsync`/`openrsync`
kompatibel und benötigt keine zusätzliche Installation:

```sh
rsync -rt --partial --progress --stats --bwlimit=5120 -e 'ssh -p 23 -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3' u676312@u676312.your-storagebox.de:greenmind-raw/ /Volumes/DEINE-SSD/GreenMind-StorageBox/greenmind-raw/
```

Bei Unterbrechung denselben Befehl erneut ausführen. Vollständig vorhandene
unveränderte Dateien werden übersprungen; unvollständige Dateien werden erneut
abgeglichen. Keine Variante mit `--delete` oder `--remove-source-files` verwenden.
Die bereits bestehende Tages-/Sensorstruktur bleibt erhalten. Keine WAVs in
GitHub oder öffentliche Verzeichnisse kopieren.

## Gesamtes aktuelles Benutzerverzeichnis einschließlich zusätzlicher Exporte

Falls auch andere Ordner oder ältere große ZIP-Exporte auf der Box benötigt
werden, erst deren Platzbedarf prüfen. Dieser alternative Aufruf kopiert das
aktuelle Benutzerverzeichnis, ausgenommen SSH-Konfiguration und Snapshots:

```sh
ssh -p 23 -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes u676312@u676312.your-storagebox.de du -sk .
mkdir -p /Volumes/DEINE-SSD/GreenMind-StorageBox/account
rsync -rt --partial --progress --stats --bwlimit=5120 --exclude='/.ssh/' --exclude='/.zfs/' -e 'ssh -p 23 -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3' u676312@u676312.your-storagebox.de:./ /Volumes/DEINE-SSD/GreenMind-StorageBox/account/
```

Nicht beide Varianten parallel ausführen; sonst wird das Archiv doppelt lokal
gespeichert. `du -sk .` kann Snapshots mit erfassen und damit größer als das
ausgeschlossene Downloadziel ausfallen. Diese Anleitung kopiert keine
SSH-Schlüssel und rollt keinen Snapshot auf der Storage Box zurück.

## Vollständigkeit des feststehenden Downloadstands prüfen

Für den Archiv-Aufruf aus dem zweiten Abschnitt:

```sh
rsync -rtcni --stats --bwlimit=5120 -e 'ssh -p 23 -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3' u676312@u676312.your-storagebox.de:greenmind-raw/ /Volumes/DEINE-SSD/GreenMind-StorageBox/greenmind-raw/
```

Das ist ein Nur-Lese-Abgleich, kein erneuter Transfer. Es sollten keine neuen
oder inhaltlich abweichenden Dateien angezeigt werden. `--checksum` liest
Dateiinhalte beider Seiten vollständig und kann lange dauern; es benutzt
rsync-Prüfsummen, ersetzt aber keinen Archiv-SHA-256-Nachweis gegen den
Originalkatalog. Während weitere WAVs hochgeladen werden, können neue Dateien
legitim erscheinen. Für einen festen Zeitpunkt stattdessen einen tatsächlich
vom Betreiber erstellten Snapshot herunterladen und denselben Snapshotpfad
auch für den Abgleich verwenden.

Beispiel nach bestätigtem Snapshot-Namen:

```sh
mkdir -p /Volumes/DEINE-SSD/GreenMind-StorageBox/snapshot
rsync -rt --partial --progress --stats --bwlimit=5120 -e 'ssh -p 23 -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes' u676312@u676312.your-storagebox.de:/home/.zfs/snapshot/VERIFIZIERTER-SNAPSHOT/greenmind-raw/ /Volumes/DEINE-SSD/GreenMind-StorageBox/snapshot/
```

Mit derselben Quelle und `-rtcni` anschließend prüfen. Das ist das Kopieren
aus einem Snapshot in ein separates Ziel, keine Wiederherstellung der Box.
Die Existenz oder erfolgreiche Prüfung dieses Snapshots wird hier nicht behauptet.

Quellen: [Hetzner SSH/rsync](https://docs.hetzner.com/storage/storage-box/access/access-ssh-rsync-borg/),
[Snapshot-Zugriff](https://docs.hetzner.com/storage/storage-box/snapshots/),
[offizielle Host-Fingerprints](https://docs.hetzner.com/storage/storage-box/general/).
