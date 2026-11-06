# Direct-Live bei Bedarf — Schritt 9

Stand: 06.11.2026. Der aktuelle `develop`-Quellstand enthält raw400 und die
Staging-Korrekturen rc4. Live wurde auf separater Staging-Hardware verwendet;
der Benutzer bestätigte die Funktion und beendete die Tests. Dauerabnahme und
Blattberührung → Bildschirm unter drei Sekunden sind damit nicht nachgewiesen.
Quellstand, veröffentlichte Downloads und Geräteverteilung bleiben getrennt;
siehe [aktuellen Entwicklungsstand](DEVELOP-LIVE.md).

## Verhalten und Datenvertrag

Jede neu geöffnete Direct-Messansicht startet mit 24 Stunden. Live aktiviert eine
eigene Vorschau mit 30, 60 oder 180 Sekunden. Sprachauswahl und Livefenster bleiben
beim Sprachwechsel erhalten. Deutsch, Englisch, Französisch und Italienisch
enthalten sämtliche neuen Bedienelemente und Statusmeldungen.

Ein Zuschauer hält eine eigene 30-Sekunden-Freigabe, erneuert alle zehn Sekunden.
Ausblenden, Verlassen oder Schliessen beendet nur seine Freigabe; bei verlorener
Beendigungsanfrage greift der Ablauf. Ohne Zuschauer beendet das Gerät die Vorschau
bei der nächsten Kontrolle, spätestens am zuletzt bestätigten Ablauf.
Die Raw-Aufzeichnung hängt nicht von diesen Freigaben ab.

Die Firmware prüft Bedarf etwa jede Sekunde, wenn der Raw-Worker verfügbar ist,
und sendet etwa alle 400 ms bis zu 16 Punkte. Bestätigte Livepakete erneuern den
Bedarf ohne zusätzliche sekündliche Kontrollabfrage. Der Browser fragt spätestens
alle 500 ms erneut ab, solange die Verbindung
funktioniert. Fehler lösen einen begrenzten Wiederholungsabstand bis fünf Sekunden
aus; veraltete Daten bleiben sichtbar gekennzeichnet. Alte Firmware erhält nach
drei Sekunden ohne Livekontrolle einen Aktualisierungshinweis. Kein automatisches
Update wird ausgelöst. 404/405 eines alten Servers begrenzen Firmwarekontrollen
auf einmal je 30 Sekunden, vorübergehendes 503 auf einmal je Sekunde.

## Signalprofile und Zeitbezug

| Profil | Eingang | Vorschau |
|---|---|---|
| raw400-box32-v1 | Nominal 400 Hz, ungefilterte Raw-ADC-Samples | Mittelwert aus 32 Samples, 12,5 Hz |
| avg20ms380-rational-v1 | Bestehendes 380-Hz-avg20ms-Signal, Roh-ADC 2 kHz | Rationaler Akkumulator 25/760, abwechselnd 30/31 Samples, 12,5 Hz |

Die 400-Hz-Boxmittelung umfasst 80 ms und hat bei nominaler Rate eine Nullstelle
bei 50 Hz. Sie ist eine Elektrodenvorschau, kein wissenschaftlicher Anti-Alias-
oder Diagnosenachweis. Gruppenlaufzeit: 38,75 ms. 380 Hz werden nicht durch einen
ungeeigneten ganzzahligen Teiler geteilt. Ein Erfassungsloch verwirft einen
unvollständigen Vorschaumittelwert; Raw-Samples werden dadurch nicht verändert.

Jeder Punkt referenziert Gerät, bestehende Raw-Sitzung, letzten Quellframe und
Anzahl gemittelter Frames. Aufnahmezeit folgt Sitzungsanker und nominaler Rate,
mit Zeitstempel am rechten Fensterrand. Die Gerätefähigkeit muss mit der bereits
gespeicherten Raw-Sitzung einschliesslich Firmware und Profil übereinstimmen.
Physische Abtastrate, Ankerfehler und reale Gesamtlatenz bleiben gesondert zu messen.

## Rail-Anteil

Rail-Samples werden vor jeglicher Filterung an den tatsächlichen ADC-Grenzen
gezählt. Ein fester Zwei-Bit-Ring speichert gültig/gesättigt für höchstens
20.000 Raw-Slots: 5.000 Bytes bei 2 kHz, zehn Sekunden. Das 400-Hz-Profil nutzt
4.000 Slots. Fehlende Erfassungen zählen nicht als gültige Messung.

Jedes Vorschaupaket enthält Raw-Anzahl, Rail-Anzahl und Start/Ende des gleitenden
zehnsekündigen Fensters. Nach Start oder Datenlücken erscheint das Fenster als
unvollständig. Fehlende Messung ist kein Nullprozentwert. Veraltete Messungen
werden gekennzeichnet. Über fünf Prozent erscheint eine Warnung, auch wenn die
gemittelte Kurve plausibel aussieht.

## Markierungen: ausdrückliche Nutzerentscheidung

**Nur live, nach drei Minuten verschwunden.** Der Mark-Knopf bindet eine Markierung
an den zuletzt empfangenen frischen Punkt, nicht an die spätere Browser-Klickzeit.
Sitzung, Frame und Aufnahmezeit bleiben gebunden; die Klickzeit ist separat.
Alle aktuell berechtigten Zuschauer desselben Sensors sehen die Markierung.
Nach höchstens 180 Sekunden wird sie aus dem RAM entfernt. Es gibt keine
SQL-Speicherung und keinen Export. Neustart leert sie sofort. Ein Sitzungswechsel
entfernt alte Markierungen; höchstens 64 existieren gleichzeitig pro Sensor.

## Gemeinsamer privater RAM-Eigentümer

Die beiden bestehenden Direct-API-Prozesse bleiben bestehen. Ein zusätzlicher,
einzelner ASGI-Prozess besitzt den flüchtigen Zustand und ist ausschliesslich
über einen privaten Unix-Socket erreichbar. Es gibt keinen zweiten Zustand
pro API-Prozess und keine neue Datenbanktabelle, Redis- oder Objektspeicherablage.

Der Socket-Ordner gehört der Dienstkennung und hat Modus 0700. Ein exklusiver
Prozess-Lock wird vor dem Binden des Sockets erworben; ein zweiter Eigentümer
darf den laufenden Socket nicht entfernen. Der Dienst hat kein Netzwerk.
Der optionale Compose-Overlay begrenzt ihn auf 128 MiB und eine halbe CPU.
Ein sekündlicher Aufräumer entfernt abgelaufene Einträge auch ohne weitere
Geräte- oder Zuschaueranfragen. Abgelaufene Markierungen werden niemals angezeigt.
Der Namensraum umfasst Umgebung, Dashboard-Origin und Organisation.
Eine gleichlautende Geräte-ID in anderem Namensraum teilt keine Live-Daten.

| Grenze | Wert |
|---|---:|
| Gleichzeitige Sensorbereiche | 32 |
| Zuschauerfreigaben gesamt | 128 |
| Zuschauer pro Sensor | 8 |
| Freigaben pro Anmeldeidentität | 16 |
| Punkte pro Sensor | 2.251 |
| Markierungen pro Sensor | 64 |
| Authentifizierungen je API-Prozess gleichzeitig | 16 |
| Vorschaupakete je Sensor | höchstens 5/s |
| Markierungsfrequenz je Identität und Sensor | höchstens 2/s |
| Maximale Paketgrösse | 4.096 Bytes |

Aktive Anmeldung, Organisations-/Zonenzugriff und Gerätestatus werden bei jeder
öffentlichen Anfrage erneut geprüft. Schreibzugriffe verlangen die exakte
Dashboard-Origin. Das Gerät nutzt seinen vorhandenen GMD-Schlüssel über HTTPS.
Anmeldegeheimnisse gelangen weder in URLs noch in den RAM-Eigentümer.
Liveantworten enthalten Cache-Control: private, no-store.
Der Eigentümer ist keine Vertrauensgrenze gegenüber anderen Prozessen derselben
Betriebskennung; sein Socket darf nur den zugehörigen API-Diensten zugänglich sein.

## Schutz bestehender Rohuploads

Raw-Ingest ruft den RAM-Eigentümer nie auf. Ausfall oder deaktivierte Livefunktion
liefern nur für Live 503; bestehende Raw-ACKs und Ablageverträge bleiben erhalten.
Geräte senden über die bereits vorhandene TLS-Verbindung des Raw-Workers, ohne
zusätzlichen TLS-Heap oder Aufnahmetask. Es wird keine zusätzliche Verbindung
parallel für Live aufgebaut. Der gemeinsame Worker priorisiert Rohdaten und
begrenzt Vorschauarbeit. Vorschau hat einen separaten 64-Punkte-Ring und verwirft bei
Überlastung Vorschaupunkte. Live-HTTP nutzt 100 ms Verbindungs-/750 ms Lesegrenzen.
Die eingebundene Arduino-Bibliothek rundet das TLS-Socket-Limit auf ganze
Sekunden: 250 ms wurden zu null Sekunden und führten im realen Staging-Test
zu Antwortfehlern und wiederholtem TLS-Neuaufbau. 750 ms ergeben ein wirksames
Ein-Sekunden-Socket-Limit; der HTTP-Lesevorgang bleibt auf 750 ms begrenzt.
Die erste Timeoutkorrektur hiess rc2; der aktuelle Staging-Kandidat heisst rc4.
Production-Quellzielkennungen bleiben rc1; das ist kein veröffentlichtes Release.
Raw-Retry behält seinen unveränderten Block; OTA wird nicht während dessen
ausstehender Übertragung gestartet. Die physische Dauer-/Heapabnahme bleibt offen.

## Bereitstellung und Rücknahme

DIRECT_LIVE_ENABLED ist standardmässig false. docker-compose.live.yml ist ein
optionaler Overlay für eine explizite Bereitstellung zusammen mit der
bestehenden Direct-Konfiguration. DIRECT_LIVE_SOCKET muss auf denselben privaten
Socket in API und Eigentümer zeigen. Beide Dienste verwenden Betriebskennung
10001. Zuerst neuen Server und RAM-Eigentümer auf Testumgebung prüfen, danach
separate Testhardware. Vor Aktivierung gelten bestehende TLS-, Organisations-
und Raw-Annahmebedingungen weiter. Rücknahme: Live deaktivieren und Eigentümer
stoppen; Raw-Ingest bleibt verfügbar.

Staging verwendet die Live-Funktion bereits. Daraus folgt keine Production-
Freigabe, Änderung der Aufbewahrung, Löschung, OTA-Verteilung oder Ersetzung
veröffentlichter Downloads. Weitere Büroboxwechsel und eine Abschaltung von
GmLive erfordern eine eigene Abnahme und abgestimmte Übergabe.

## Nachweise und verbleibende Abnahme

Automatisierte Verträge prüfen unabhängige Freigaben, Grenzen, exakte 12,5 Hz,
Zählerüberlauf, Raw-Bytegleichheit trotz voller Vorschau, Rail-Fenster, Frische,
Markerablauf, Zugriffsverlust, zwei HTTP-Worker sowie echten privaten Socket mit
Exklusivität und Neustart. PostgreSQL-Prüfungen laufen in GitHub CI; die Oberfläche
wird in allen vier Sprachen gebaut und getestet.

Software-/Simulatortests ersetzen keine physische Blattberührung. Offen bleiben:
400-Hz-Hardwareabnahme, reale Rate und Zeitfehler, WLAN-/TLS-Latenz, erster Punkt
und Berührungsreaktion jeweils unter drei Sekunden, Heap und Langzeitverhalten
unter Raw-Rückstand sowie Freigabeablauf auf separater Testhardware.


## Lückenfreie Übertragung bei begrenzter Netzverzögerung

Der Staging-Kandidat rc3 übernimmt bis zu 16 echte Vorschauwerte in ein Paket.
Der Server bestätigt das Paket samt verbleibendem Zuschauerbedarf. Solange
solche Bestätigungen eintreffen, entfallen zusätzliche sekündliche Kontrollabfragen
auf derselben TLS-Verbindung. Alte Geräte verstehen die bisherige Statusangabe
weiterhin; die Vergrösserung der serverseitigen Paketgrenze ist additiv.

Ein vorbereitetes Paket bleibt bis zur Bestätigung unverändert erhalten.
Verlorene und beschädigte Antworten führen zur exakt identischen Wiederholung
mit derselben Sequenz und demselben Rail-Fenster. Die serverseitige Duplikatprüfung
verhindert doppelte Werte. Ein vorübergehender Kontrollfehler widerruft keinen
bereits bestätigten Zuschauerbedarf. Abgelaufene oder freigegebene Zuschauer
werden durch Paketantwort oder nächste Kontrollabfrage weiterhin durchgesetzt.

Der Puffer ist fest auf 64 Punkte begrenzt. Vorbereitete Pakete werden höchstens
1,5 Sekunden wiederholt, unversandte Werte bei Raw400 höchstens 4,5 Sekunden
(im bisherigen 380-Hz-Pfad zwei Sekunden) nachgeholt. Längere Ausfälle erzeugen
weiterhin echte, erkennbare Lücken; es werden keine Messwerte ergänzt.
Der native Netzwerktest führt das tatsächliche Firmwaremodul mit 450–650 ms
Antwortzeit, verlorenen/beschädigten Bestätigungen und paralleler echter
ADC-Erfassung aus. Er verlangt fortlaufende Vorschauframes sowie identische
Rohdaten und kontrolliertes Abschalten ohne Zuschauer.

## Gemeinsamer Worker ohne lange Raw-Blockade (Staging rc4)

Bei aktivem Direct-Live erhält auch der Rohupload eine 750-ms-Lesegrenze,
250-ms-Verbindungsgrenze und fünf Sekunden TLS-Handshake-Zeit. Dies sind Grenzen
einzelner Netzwerkoperationen, keine garantierte Gesamtlatenz. Ohne Zuschauer und
in DUAL bleiben die bisherigen Raw-Grenzen unverändert. Ein fehlgeschlagener
Rohupload bleibt bis zum passenden ACK unverändert im Spool; Wiederholung folgt
bei aktivem Live nach 500–599 ms statt wachsendem Backoff.

Während dieser Wiederholpause darf Live dieselbe TLS-Instanz neu verbinden.
Nach einem Raw-ACK darf ein begrenzter Vorschauaufruf auch bei kleinem Rückstand
(bis 8 KiB) laufen; grösserer Rückstand priorisiert ausschliesslich Rohdaten.
Es gibt weiterhin genau einen Netzwerk-Worker und keine zweite TLS-Instanz.
Bei aktivem Direct-Live wird WLAN-Modem-Sleep ausgeschaltet, damit empfangene
Antworten nicht auf den nächsten DTIM-Zyklus warten. Nach Ende des bestätigten
Zuschauerbedarfs wird die normale Stromsparregel wiederhergestellt. Ein
fehlgeschlagener Wechsel wird erneut versucht; DUAL/Gateway werden nicht geweckt.
Die Erfassung läuft unabhängig weiter. Der native Test führt den tatsächlichen
Worker mit Rohupload-Timeouts, identischen Wiederholungen, verlorenen Live-ACKs,
fortlaufenden Vorschauframes und unveränderten PCM-Werten aus.
