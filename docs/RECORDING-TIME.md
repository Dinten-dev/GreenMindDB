# Schritt 10: Aufnahmezeit und Datenabdeckung

Softwarekandidat vom 03.11.2026, auf Schritt 9 aufgebaut. Kein Deployment,
keine Migration und keine Änderung von Aufbewahrung oder Originaldaten.

## Aufnahmezeit, Empfang und Zeitzone

Direct rekonstruiert Aufnahmezeit aus Sitzungsanker, Sampleindex und nominaler
Abtastrate. Verwendet wird dieselbe Ganzzahl-Zeitrechnung wie bei der bestehenden
WAV-Erzeugung. Empfangszeit oder Zeitpunkt der Aufbereitung ersetzen nie einen
fehlenden Anker. Der Gerätestatus zeigt den Empfang separat.

Die Oberfläche nennt Aufnahmebereich, IANA-Zeitzone und UTC-Offset. Der Offset
unterscheidet insbesondere die doppelte Ortsstunde beim Wechsel zur Winterzeit.
Historische Gateway-Zeitstempel ohne nachgewiesenen Anker bleiben ausdrücklich
ungesichert. Auch ein vorhandener Direct-Zeitanker zertifiziert keine physische
Uhrgenauigkeit. V1 liefert einen deklarierten Sitzungsanker; V2 meldet zusätzlich
NTP-Quelle und gegebenenfalls eine Unsicherheit. Diese Angaben ersetzen keine
unabhängige Messung. Geräte- oder Serverempfangszeit wird nicht als Anker erfunden.

Die Diagrammzeiten sind bei Übersichten gerasterte Aufnahmeintervalle. Die
Detailansicht einer Originalaufnahme nennt Sitzung, erstes Sample und SHA-256.
Manifestintegrität wird beim Auflisten und Herunterladen geprüft; beim Download
prüft der vorhandene WAV-Leser zusätzlich Datei-Hash, Format und Samplezahl.
Ein Hash bestätigt Dateiintegrität und Zuordnung, keine physische Zeitgenauigkeit.

## Datenabdeckung und laufende Aufnahmen

Zusätzlich zur bisherigen Darstellung von Lücken enthält die Übersicht:
- Abdeckung des tatsächlich angeforderten Zeitfensters;
- fehlende Zeitbereiche einschliesslich leerer Ränder;
- laufende Direct-Sitzungen anhand noch nicht versiegelter Segmente;
- unbekannte Abdeckung bei fehlenden Zählern oder überlappenden Intervallen;
- getrennten Hinweis auf ungeprüfte physische Zeitgenauigkeit.

Die Abdeckung wird aus vorhandenen Übersichtsintervallen berechnet. Bei
Randintervallen ist sie eine Schätzung anhand der jeweiligen Bucket-Abdeckung;
fehlende Werte werden nicht als null interpretiert. Eine vollständig abgedeckte
Übersicht ist kein Vollständigkeitsnachweis für Rohaufzeichnungen. Vorhandene
Manifest-Samplezählung und Kennzeichnung laufender, unvollständiger oder erst
später gestarteter Originalaufnahmen werden wiederverwendet.

Zeitkontext liest höchstens 128 Sitzungsreferenzen pro Anfrage. Bei mehr
Sitzungen wird der Kontext als unvollständig/ungesichert ausgewiesen.
Zugriffsprüfung auf Organisation und Zone sowie begrenzte historische Leser
bleiben erhalten. Archiv-/Payload-Routen des aktuellen Empfängers bleiben
bestehen; die Publikationshistorie bekommt keine fremde Archivkonfiguration.

## Export

Direct-Diagramm und CSV teilen dieselben expliziten UTC-Zeitgrenzen. UTC-Dateinamen
enthalten Start und Ende mit Mikrosekunden sowie Gerätebezug. CSV erhält additive
Spalten für Zeitbasis, Ankerstatus und nicht zertifizierte physische Genauigkeit.
WAV-Downloads verwenden die bestehende UTC-/Sitzungs-/Samplebenennung des aktuellen
Empfängers, auch für die Publikationshistorie. Der gespeicherte Objektname und
sämtliche WAV-Bytes bleiben unverändert.

Gateway-ZIP-/WAV-Namen nennen ebenfalls UTC mit Mikrosekunden. Der ältere
Haupt-API-CSV behält seine bisherige Ortszeitspalte, benennt deren Zeitzone
und ergänzt eine eindeutige UTC-Spalte. Der authentifizierte Lese-Sidecar verwendet
bereits UTC-Spalten. Markierungen bleiben ausschliesslich im flüchtigen Livepuffer
und werden weiterhin nicht exportiert.

## Prüfungen und Grenzen

Automatische Tests decken Integer-Samplezeit, fehlenden Anker, Teilabdeckung,
leere Ränder, Überlappung, doppelte Winterzeitstunde, UTC-Dateinamen und identische
Diagramm-/Exportgrenzen ab. PostgreSQL prüft Projektion, Manifeständerung,
Dateiidentität und unveränderte Original-PCM-Bytes. Die zusätzlichen
Bedienelemente und Hinweise sind auf Deutsch, Englisch, Französisch und
Italienisch vorhanden.

Die getrennten GitHub-Entwürfe bauen auf #31 und #32 auf. Physische Zeitgenauigkeit,
400-Hz-Hardwareabnahme und reale Blattberührungs-Latenz bleiben gesonderte
Messaufgaben. Ein lokaler USB-Testlauf wird separat dokumentiert und ersetzt
keine Produktionsfreigabe. Peters Büroboxen bleiben unberührt.
