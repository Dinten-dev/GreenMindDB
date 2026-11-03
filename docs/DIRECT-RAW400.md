# Direct raw400 – Schritt 8, Softwarekandidat

Firmwarecode, Buildziele und `tools/`-Pfade beziehen sich auf
[GreenMindArdu](https://github.com/Dinten-dev/GreenMindArdu/tree/feat/direct-raw400-20261103).
Dieses Repository enthält die kompatible Receiver-Erweiterung.

03.11.2026. Zielrate **400 Hz**, nominaler Abstand **2500 µs**.
Keine Geräte geflasht, keine OTA-Zuweisung, kein Release veröffentlicht.
**Physische Rate, Jitter, Speicherstabilität und 24-h-Dauertest bleiben offen.**

## Erfassung und Profile

Nur `DIRECT` verwendet `ad8232-raw400-v1`: einzelne echte 12-Bit-ADC1-Werte
von GPIO4, ohne Notch, Mittelung, Tiefpass oder Dezimation vor Speicherung.
`GATEWAY` und `DUAL` behalten den bestehenden 2-kHz-DMA-/avg20ms-/380-Hz-Pfad;
die separate Gateway-Firmware bleibt unverändert. Vorschauübertragung ist nicht
Teil dieses Schritts; Aufnahme besitzt keine Zuschauer- oder Live-Abhängigkeit.

Das gepinnte ESP32-S3-SDK setzt für kontinuierliche ADC-DMA mindestens 611 Hz
voraus (`soc_caps.h`). Daher verwendet raw400 den periodischen `esp_timer`-Task
mit 2500 µs und unterdrückten Nachholereignissen. Die tatsächliche Rate muss auf
der Zielhardware gemessen werden; Timerkonfiguration ist kein Gleichmässigkeitsnachweis.

Der Callback liest genau einmal und legt einen eigenen Wert in eine begrenzte
SPSC-Warteschlange (256 Werte, nominal 640 ms). Er führt keine Netzwerkoperation,
Dateioperation oder eigene dynamische Speicherallokation aus. Die Aufnahmeaufgabe
übernimmt PCM und den vorhandenen Upload-Spool. WLAN/TLS besitzen einen separaten
Konsumenten. Zu frühe Nachholereignisse erzeugen keine ADC-Ersatzwerte.
Verspätungen, ungültige ADC-Lesungen und volle Puffer hinterlassen Samplelücken.
Ein Lückenbeginn schliesst den bisherigen Chunk; WAV-Runs überbrücken keine Lücke.

Der erste echte Messwert verankert den nominalen Sampleindex. Die bestehende
SNTP-/Monotonic-Zuordnung und persistente Bootidentität bleiben erhalten.
Protokoll 1 bleibt Standard, Protokoll 2 bleibt explizit opt-in. Physische
Zeitunsicherheit bleibt unbekannt. Jeder Boot erzeugt eine neue Sitzungs-UUID;
ein Profilwechsel innerhalb derselben Server-Sitzung wird abgewiesen.

## Skalierung und Rails

GreenMind-Skalierung bleibt nominal 0..3300 mV am ADC-Ausgang -> 0..32767 PCM16
(`unsigned-mv-linear-int16-v1`). Das ist keine kalibrierte Elektrodenspannung.
Alle 4096 ADC-Codes bleiben verschieden; 0 und 4095 bleiben 0 und 32767.
Es gibt keinen mV×10-Zwischenschritt im gespeicherten Signal: dieser würde oberhalb
3276,7 mV an der signed-int16-Grenze begrenzen. Zehntel-mV dienen nur der Anzeige.
Ungültige ADC-Codes werden verworfen und als Lücke erkennbar, nicht geklemmt.

Analoge Rail-Definition: `raw == 0 || raw == 4095`, vor jeder Anzeigeverarbeitung.
Nenner sind gültige echte ADC-Konversionen im gemessenen Diagnosefenster,
üblicherweise etwa zehn Sekunden. Die Ausgabe enthält Samplezahl, Rail-Anzahl,
Fensterstart/-ende und Rail-Prozent; ohne Samples lautet Prozent -1 (unverfügbar).
`scale_clipped=0` beschreibt die verlustfrei unterscheidbare Abbildung gültiger
12-Bit-Codes in diesem Profil, getrennt von analogen Rails und `raw_invalid`.
Fehlende Timer-Slots, verworfene Warteschlangenwerte und Transportverluste besitzen
separate Zähler. ADC- und Callback-Dauer, Samplejitter, längstes Intervall sowie
freier und minimaler Heap werden berichtet. Diagnosefenster werden gemeinsam
unter kurzer Sperre übernommen; Netzwerk und serielle Ausgabe liegen ausserhalb.

## Transport und Dateikompatibilität

400 Frames bilden nominal eine Sekunde und 800 PCM-Bytes. Framezähler, Chunk-
Metadaten, Prüfsumme und WAV-Header verwenden 400. Das neue optionale Feld
`signal_profile` ist Bestandteil der unveränderlichen Sitzung und der Chunkidentität.
Der bestehende Spool speichert dieses Profil gemeinsam mit den Bytes bis zum
exakten ACK; seine Kapazitätsanzeige verwendet die tatsächliche Rate.
PSRAM bleibt flüchtig. Nicht-PSRAM-Geräte behalten den ausdrücklich begrenzten
32-KiB-RAM-Fallback, falls dessen Heapreserve verfügbar ist. Kein pauschales
120-Sekunden-Pufferversprechen für diese 8-MiB-Flash-Variante.

Backend-Erweiterung zuerst einführen. Alte Server lehnen das neue Profil ab;
der Upload-Spool hält die nicht bestätigten Bytes. Alte Sitzungs-Konfigurationen
und Duplicate-Hashes bleiben bytegleich berechenbar, einschliesslich V2-Nullwerten
für unbekannte Zeitunsicherheit. Vorhandene 380-Hz- und HPK-100-Hz-WAV-Dateien
behalten Header, Samples und ursprüngliche Verarbeitung. Ein 100-Hz-HPK-File wird
nicht durch Umbenennen ungefiltert oder zu einem raw400-Profil. HPK-mV×10 und
GreenMind-PCM besitzen verschiedene Skalierungen; fehlende Kalibrierung nicht erfinden.

## Referenzen

HPK-Referenz: bereitgestellte `biolingo_field_wifi.ino`, 400 Hz ohne vorgeschaltete
Speicherfilterung. Peters GmLive ist ein anderer Abgriff: Vorschau aus 380-Hz-
avg20ms-Frames; Rails aus den rohen 2-kHz-Konversionen. Sein Netzwerkdienst und
seine Zugangsdaten werden nicht übernommen. Abgeglichen am GitHub-Commit
`d0e62a2a7bbf2dde339f759ecbed6cd032f6045f` unter
[elektrode-live README](https://github.com/pgloor/greenmind-live/blob/d0e62a2a7bbf2dde339f759ecbed6cd032f6045f/electrode-live/README.md).
Die lokale alte `greenmind-live-main`-Kopie enthält diesen neuen Ordner nicht.

## Softwareprüfung und separate Hardwareabnahme

Native Tests prüfen alle 4096 Codes, obere Grenzwerte, Sampletakt über den
32-Bit-micros-Überlauf, Nachholereignisse, Lücken, Profilidentität und ACK-Spool.
Backend-Tests prüfen echte HTTP-Annahme, Wiederholung, WAV-Bytes/Rate, mV-
Dekodierung, Lückentrennung, Sitzungswechsel und 100/380/400-Hz-Altdateien.
PostgreSQL prüft das Profil und parallele Upload-Wiederholungen.

Separate Testhardware verwenden, zuerst Staging. Generic-8-MiB- und N16R8-
Konfiguration getrennt abnehmen; tatsächlichen Flash und PSRAM feststellen.
`direct_biolingo_raw400_adc_selftest` ist ein eigener Offline-Prüfbuild ohne
Netzwerk-/Provisionierungszugriff. Er ist keine Download- oder OTA-Version.
Alle Builds sind Kandidaten mit neuer Versionskennung, keine früheren akzeptierten Images.

Vor Einführung: 24 Stunden mit Referenzsignal und echter WLAN/TLS-Last messen,
alle Samples mit Datei/Hash/Index vergleichen, hohe und niedrige Rails einspeisen,
WLAN-Unterbruch innerhalb der tatsächlich gemessenen Pufferkapazität prüfen.
Upload-ACKs, Wiederanlauf, Dateigrenzen und Heaptrend getrennt dokumentieren.
Rohverlust, Neustart oder Puffervollstand dürfen nicht als Vollständigkeit gelten.

`tools/check_raw400_capture.py <serial.log>` prüft ausschliesslich vorhandene
Diagnoseausgaben, ohne das Gerät anzusprechen. Startkriterien: mindestens 24 h
zusammenhängende Fenster, 400±0,5 Hz je Fenster, höchstens 625 µs Rasterabweichung,
höchstens 1250 µs ADC-/Callback-Dauer, mindestens 64 KiB interne Heapreserve und
keine Erfassungs-/Transportverluste. Kriterien sind explizite Testbudgets, keine
behaupteten Messergebnisse. Das Werkzeug erteilt niemals eine Releasefreigabe;
Dateitreue, Signalreferenz und Speichertrend erfordern zusätzliche Belege.

Die drei bisherigen Direct-Paketierer sind für diesen Kandidaten gesperrt, damit
kein raw400-Binary als 380-Hz-/avg20ms-Release etikettiert wird. Neue Paket-/OTA-
Einführung erst nach vollständiger Hardware- und Receiver-Abnahme. Keine Tags,
kein `direct-latest`, keine OTA-Kampagne und kein Eingriff in Peters vier Büroboxen.
Zwei ihrer Kennungen sind bekannt (94b8/94cc); ohne vollständige Zuordnung keine
Freigabe für einen Büroboxwechsel. GmLive bleibt bis zum nativen Ersatz bestehen.
