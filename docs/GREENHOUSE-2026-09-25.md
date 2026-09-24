# Feldvorbereitung für 25. September 2026

## Festgelegte Quellen

| Komponente | Repository | Geprüfter Quellstand |
| --- | --- | --- |
| Biolingo Gateway-Sensor 1.1.1; Direct 2.7 Staging/Production | Dinten-dev/GreenMindArdu | `ae645ebc8a6818f37d1e6c39e37ea47c669f5760` |
| Raspberry-Pi-Gateway | Dinten-dev/GreenMindRPIv1 | `c6c34e452a15024c624c4c896416eab7a0b7db14` |
| Unveränderter Cloud-Anwendungscode | Dinten-dev/GreenMindDB | `2fbd207f86637cf21ef2f6f7f3cc40ebf295b423` |

Diese Datei ist Dokumentation, keine Servermigration. Firmware und Gateway sind
Feldkandidaten auf develop, noch ohne physische Abnahme. Keine laufenden Gateways,
Sensoren, Empfangsdienste, Datenbanken oder Production-Routen wurden dafür geändert.

## Heute nachgewiesen

- Alle drei ESP32-Builds erfolgreich, native Transport-/ACK-/Uhrüberlauftests bestanden.
- Gateway: 142 Tests bestanden, danach drei zusätzliche Tests für geschützten
  Reset und bewusste lokale Registrierung bestanden; abschließender GitHub-Lauf prüft alles zusammen.
- Gateway-Lastprüfung: zwölf simulierte Sensoren, 360 Pakete, 36 Wiederholungen,
  genau 136800 WAV-Samples. Auf dem Mac, keine Leistungszusage für Pi/SD-Karte.
- Cloud-Unit-Tests: 287 bestanden, vier Docker-Prüfungen übersprungen,
  38 Integrationsfälle dort ausgeklammert.
- Separat mit lokaler PostgreSQL/Timescale-Datenbank: 35 Tests bestanden,
  ein optionaler Datenbank-Neustarttest übersprungen. Einschließlich Direct-
  Parallelität, Legacy-Migrationen, Visualisierung und Proxy-Rücknahme.
- Frontend: 36 Tests, Lint und Typprüfung bestanden.
- Production lesend: Peter-buero1 mit drei frischen Sensorströmen, Reihe-28 mit acht.
  Datenträger 68 % belegt, ungefähr 24 GiB frei.

## Offene Befunde

- Keine USB-Sensorhardware war am 24.09.2026 sichtbar. Pi-Modell, Sensoranzahl,
  exakter Zonenname und Hardware-Abnahme wurden noch nicht bestätigt.
- Direct-Testsensor offline; gespeicherte Nutzdaten und WAV-Prüfsummen passen,
  aber kein neuer Direct-Hardwareversand nachgewiesen.
- Letzte vollständige Büro-WAVs hatten nur ungefähr 61–87 % Zeitabdeckung.
  Reihe-28 ungefähr 100 %; ein Sensor lag am ADC-Maximum von 3300 mV. Empfang
  und biologisch brauchbare Signalqualität sind getrennt zu prüfen.
- Direct besitzt etwa neun Sekunden RAM-Puffer, kein dauerhaftes Offline-Archiv.
- Reale WLAN-Unterbrechung, Pi-/Sensor-Neustart und Stromverlust bleiben offen.
- Der bisherige administrationsbezogene Releaseprüfer vergleicht Benutzerkonten
  mit seinem alten Veröffentlichungssnapshot. Er schlug nach zwischenzeitlichen
  Kontoänderungen erwartbar an; er ist keine aktuelle Feldabnahme.

## Morgen: sichere Reihenfolge

1. Firma und neue Zone im Dashboard bestätigen; berechtigte Benutzer zuordnen.
   Es wurde keine Zone mit geratenem Namen erzeugt.
2. Neue Pi-Installation mit exakt obigem Commit. Mehr als 8 GiB freie
   Reserve, 64-Bit-Bookworm und eigenes Netzteil prüfen. Bestehende Pi-Daten
   sichern, bevor auf vorhandener Hardware installiert wird.
3. Zuerst Gateway registrieren. Seine lokale Health-API muss Protokoll 3 und
   Sequenzbestätigungen melden; Gateway-Sensor 1.1.1 erst danach einsetzen.
4. Einen Biolingo-Gateway-Sensor per BLE mit WLAN versorgen. MAC und frischen
   Gateway-Sensor-Code über `python -m tools.register_sensor --gateway-ip ...`
   zuordnen. BLE-PoP, Gateway-Sensor-Code und Direct-Code sind verschieden.
5. Direct-Production-Firmware über USB installieren, vorher vollständiges
   geschütztes Flashbackup. Hotspot `GreenMind-Sensor-XXXX`, `192.168.4.1`,
   WLAN und Direct-Code aus derselben Production-Zone eingeben.
6. Beide Wege zehn Minuten beobachten: tatsächliche Messdaten und abrufbare
   380-Hz-WAVs, richtige Zone/Rechte, steigende ACKs, keine stetigen Verlustzähler.
7. Kurze WLAN-/Cloud-Unterbrechung und Wiederanlauf getrennt prüfen. Bei
   Dauerverlusten, fehlenden Dateien oder falscher Zone die Ausweitung stoppen.

Die Firmware-Anleitung liegt in GreenMindArdu unter
`docs/GREENHOUSE-2026-09-25.md`, die Pi-Anleitung in GreenMindRPIv1 unter
`greenmind-gateway/docs/GREENHOUSE-2026-09-25.md`. Feldpakete enthalten
Quellcommit, Flash-Adressen und SHA-256-Prüfsummen; kein WLAN-Passwort oder Token.
