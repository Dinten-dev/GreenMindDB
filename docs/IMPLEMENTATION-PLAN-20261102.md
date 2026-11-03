# GreenMind: Umsetzung mit gezielter Subagenten-Unterstützung

## Arbeitsaufteilung

- Hauptagent übernimmt Architektur, Sicherheitsbewertung, Fehleranalyse und Integration.
- Subagenten dürfen unabhängige Routine- und Mengenaufgaben parallel bearbeiten.
- Ausschliessliches Subagenten-Modell: **GPT-6.1 Terra**, Reasoning **xhigh**.
- Keine anderen Modelle oder Reasoning-Einstellungen für Subagenten verwenden.
- Maximal drei Subagenten gleichzeitig; Hauptagent bleibt koordinierend.
- Jeder Auftrag erhält begrenzten Kontext und eindeutige Abnahmekriterien.
- Schreibaufgaben erhalten getrennte Dateien oder isolierte Arbeitsstände.
- Hauptagent prüft sämtliche Ergebnisse vor ihrer Integration.
- Subagenten führen keine eigenständigen Deployments oder Firmware-Verteilungen durch.

| Für Subagenten geeignet | Beim Hauptagenten |
|---|---|
| Fundstellen und fest eingebaute Texte inventarisieren | Architektur und Datenverträge |
| Übersetzungsschlüssel nach festgelegtem Schema übertragen | Sicherheitsbefunde und Schutzkonzepte |
| Freigegebene Texte in vier Sprachdateien einpflegen | Login- und Zeitrekonstruktionsanalyse |
| Bestehende Komponenten nach festgelegtem Muster vereinheitlichen | Firmware-Erfassung und Signalverarbeitung |
| Definierte Testfälle und Testdaten ergänzen | Nebenläufigkeit und Datenmigrationen |
| Unabhängige Prüfungen ausführen und Ergebnisse dokumentieren | Integration, Bewertung und Freigabevorbereitung |

## Ergänzung nach Peters Live-Übergangslösung

- Ziel: Elektrodenplatzierung mit zeitnaher, ausreichend dichter Signalansicht unterstützen.
- Neue Anforderungen ersetzen widersprechende bisherige Live- und Markerfestlegungen.
- Standardansicht bleibt 24 Stunden; Live erhält eigene kurze Fenster.
- Peters Beispiel dient als Referenz, nicht als GreenMind-Architektur.
- Native Livevorschau bleibt im GreenMind-Direct-Dienst mit GreenMind-Zugriffsschutz.
- Rohaufzeichnung und bestehende Uploads laufen unabhängig von Zuschauern weiter.

### Übergang und geschützte Büroboxen

- Peters Übergangsansicht: https://gmlive.biolingo.org
- Laut Peter verwenden aktuell alle vier Büroboxen seinen Build.
- Diese vier Boxen bleiben bis zur fertigen Ersatzversion OTA-gesperrt.
- Keine automatischen oder allgemeinen Rollouts auf diese Geräte anwenden.
- Vor Firmwarearbeiten alle vier Geräte eindeutig inventarisieren und zuordnen.
- Bekannt genannt: 94b8 und 94cc; weitere Gerätekennungen noch zuordnen.
- Aktuelle Buildkennung und benötigte GmLive-Unterstützung pro Gerät festhalten.
- Zunächst separate Testhardware verwenden; Büroboxen bleiben im Übergangsbetrieb.
- Pilotwechsel erst nach fertiger, geprüfter Version und konkreter Produktionsfreigabe.
- Rückfall muss bei Büroboxen auch deren bisherigen GmLive-Betrieb wiederherstellen.
- GmLive erst nach erfolgreicher nativer Abnahme und abgestimmter Übergabe abschalten.
- Rückmeldung an Peter nach Abnahme vorbereiten; Versand separat abstimmen.

### Quellen und Einordnung

- Referenz: https://github.com/pgloor/greenmind-live
- Relevantes Unterverzeichnis: electrode-live/
- Lokale Referenz: bereitgestellter Ordner greenmind-live-main/
- Diese Kopie enthält Dashboard und SQL-Abgriff, aber kein electrode-live/.
- GmLive.h fehlt ebenfalls; neuer Live-Abgriff bleibt gesondert abzugleichen.
- Der verfügbare GitHub-Zugriff liefert derzeit HTTP 404.
- Code und README müssen vor Übernahme gezielt abgeglichen werden.
- Folgende Übergangsdetails stammen aus Peters Mitteilung, nicht eigener Laufzeitprüfung.
- GmLive.h wird nur mit GREENMIND_GMLIVE=1 eingebunden.
- Der beschriebene Patch basiert auf GreenMindArdu 9dc2a49.
- Er sendet 12,5-Hz-Mittelwerte des bestehenden avg20ms-Signals.
- Rail-Anteile werden in dieser Übergangslösung alle zehn Sekunden gesendet.
- Der Biolingo-Server hält drei Minuten im RAM bereit.
- Die HTML-Ansicht fragt jede Sekunde neue Werte ab.
- Peter meldet unveränderte GreenMind-Uploads und stabilen Betrieb auf 8-MB-Boxen.
- Gemeldeter freier Heap: vorher 121 kB, danach 94 kB.
- Diese Einzelbeobachtung ersetzt keine eigene Speicher- oder Dauerabnahme.
- Gemeldete Rail-Anteile um 80 Prozent betreffen 94b8 und 94cc.
- Eine plausible gemittelte Kurve beweist keine übersteuerungsfreie Erfassung.

## Umsetzungsschritte

### 1. Ausgangsstand festhalten

- Aktuelle Backend-, Frontend-, Gateway- und Firmwarestände abgleichen.
- Vorhandene Sicherheits-, Login- und Zeitstempeländerungen berücksichtigen.
- CSV-Befunde gegen den vorgesehenen Ausgangsstand erneut abgleichen.
- Änderungen isolieren; bestehende Arbeitsstände erhalten.
- Bisherige Analyse war statisch; Produktionsstatus bleibt ungeprüft.
- Schritt 1 ist im Ausgangsstandsbericht vom 02.11.2026 dokumentiert.
- Vorhandene Arbeitsstände und abgeschlossene Prüfsummen bleiben unverändert erhalten.
- Zusatzinventar der vier Peter-Builds bleibt vor Firmwarearbeiten offen.
- GmLive-Patch gegen 9dc2a49 mit Kandidat 24e28d9 abgleichen.
- Vorhandene Zeit-, WLAN- und Sitzungsschutzkorrekturen dabei erhalten.

### 2. Öffentliche Schnittstellen absichern: S1 und S2

- Alte Gateway-Schlüssel verursachen potenziell geräteübergreifenden Prüfaufwand.
- Vorhandene Begrenzungen auf sämtlichen erreichbaren Routen überprüfen.
- Alte Schlüssel schrittweise auf direkt auffindbare Schlüssel migrieren.
- Verbleibende aufwendige Prüfungen zentral begrenzen.
- JSON-Grössen vor dem Parsen begrenzen, einschliesslich gestreamter Anfragen.
- Öffentliche Textfelder und Listen zusätzlich begrenzen.
- WAV- und Fotouploads erhalten passende eigene Grössenlimits.

### 3. Gerätekopplung korrigieren: S3 und S6

- Sensor- und Gateway-Codes eindeutig typisieren.
- Registrierung prüft Codetyp, Zone, Ablauf und Einmalverwendung.
- Codeeinlösung und Geräteanlage innerhalb derselben Transaktion sperren.
- Bestehende untypisierte, unbenutzte Codes bei Migration ungültig machen.
- Direct-Sperren und sichere Wiederholungen erhalten.
- Umsetzung und kontrolliertes Datenbank-Upgrade: [TYPED-PAIRING.md](TYPED-PAIRING.md).
- Vorbereitung erfolgt isoliert; Produktionsfreigabe und OTA sind nicht enthalten.

### 4. Öffentlichen Zugang widerrufen: S4 und S5

- Pro Pflanze höchstens einen aktiven öffentlichen Zugang erlauben.
- Parallele Erstellung durch Datenbanksperre und Eindeutigkeit absichern.
- Bestehende Mehrfachzugänge sperren und kontrolliert neu ausstellen.
- Widerruf deaktiviert sämtliche zugehörigen Zugänge und Sitzungen.
- Jede Sitzungsprüfung berücksichtigt zusätzlich den übergeordneten Zugang.
- Umsetzung und Einführung: [PUBLIC-ACCESS-REVOCATION.md](PUBLIC-ACCESS-REVOCATION.md).
- Laden und Drucken bleiben lesend; Neuausstellung benötigt Bestätigung.

### 5. Exportbereinigung ergänzen: S7

- Temporäre Rohkopien nach Verarbeitung oder Fehler entfernen.
- Abgebrochene und verwaiste Exportreste kontrolliert bereinigen.
- Fertige Exporte nach bestehender 24-Stunden-Gültigkeit entfernen.
- Laufende Downloads und aktive Aufträge durch Sperren schützen.
- Speicherquoten berücksichtigen sämtliche zurückbehaltenen Exportdateien.
- Bereinigung ausschliesslich auf erzeugte Exportkopien begrenzen.

### 6. Web-Login reparieren

- Fehler anhand Anmeldung, Neuladen und Weiterleitung reproduzieren.
- Sprachpräfix bei sämtlichen Anmeldeweiterleitungen erhalten.
- Loginseite und Navigation verwenden denselben Anmeldestatus.
- Sitzungsablauf, Netzwerkfehler und falsche Zugangsdaten unterscheiden.
- Ladezustand, Passwortanzeige und verständliche Fehlermeldungen ergänzen.
- Bestehende Sitzungsschutzfunktionen erhalten.

### 7. Gateway-Entfernung reparieren

- Schaltfläche als „Gateway entfernen“ beschriften.
- Gateway deaktivieren und seinen Zugang widerrufen.
- Wiederholtes Entfernen bleibt gefahrlos.
- Messungen, Sensorhistorie und Zuordnungsnachweise erhalten.
- Aktive Listen aktualisieren; betroffene Sensoren als getrennt anzeigen.
- Fehlende Berechtigungen verständlich melden.

### 8. Direct-Firmware auf 400 Hz umstellen

- HPK-Erfassungsreferenz und GmLive-Abgriff als unterschiedliche Quellen behandeln.
- GmLive beschreibt bestehendes avg20ms; HPK beschreibt ungefilterte 400-Hz-Speicherung.
- Genau 400 ungefilterte Messwerte pro Sekunde als Zielrate umsetzen.
- Tatsächliche Rate, Gleichmässigkeit und Ausfälle auf Hardware nachweisen.
- Rohaufzeichnung unabhängig von Liveansicht und Vorschauübertragung fortführen.
- Bestehende GreenMind-PCM-Skalierung verwenden; Übersteuerung vorher erfassen.
- Peters mV×10-Konvertierung wegen möglicher Wertebegrenzung anpassen.
- Rail-Zähler aus ADC-Rohwerten vor Mittelung und Filterung bilden.
- Analoge ADC-Sättigung und digitale Skalierungsbegrenzung getrennt erfassen.
- Grenzschwellen, Messfenster, Nenner und Signalprofil dokumentieren.
- Samplezähler, Puffergrössen und WAV-Metadaten konsistent aktualisieren.
- Signalprofil versionieren; Firmwarewechsel startet eine neue Aufnahmesitzung.
- Alte 380-Hz- und HPK-100-Hz-Aufnahmen weiterhin korrekt lesen.
- Bestehende Gateway-Firmware bleibt zunächst kompatibel unterstützt.
- Auf 8-MB-Boxen freien Heap und längste Blockierzeiten prüfen.
- Büroboxen bis zur freigegebenen Ersatzversion vom OTA ausschliessen.

### 9. Live bei Bedarf einschalten

- Standardansicht bleibt **24 Stunden**.
- Auswahl **Live** fordert eine zeitlich begrenzte Vorschau an.
- Ziel: weniger als drei Sekunden von Blattberührung bis Bildschirm.
- Erste Livewerte ebenfalls innerhalb drei Sekunden unter stabilen Testbedingungen liefern.
- Etwa zwölf Werte pro Sekunde liefern; Zielrate 12,5 Hz.
- Vorschau und Rohaufzeichnung besitzen getrennte Puffer und Signalprofile.
- Peters Mittelungsansatz anhand des tatsächlichen GmLive-Codes nachvollziehen.
- Für 400-Hz-Rohdaten passende Vorschau und Filterung gesondert festlegen.
- Alte 380-Hz-Profile nicht über ungeeignete Ganzzahldivisoren umsetzen.
- Livebedarf spätestens jede Sekunde prüfen oder gleichwertig ereignisgesteuert empfangen.
- Als Startkonfiguration Vorschaupakete spätestens alle 500 Millisekunden senden.
- Browser-Aktualisierungsintervall höchstens 500 Millisekunden; alternativ direkte Zustellung.
- Gesamtlatenz einschliesslich Verarbeitung, Netzwerk und Darstellung messen.
- Frühere Fünfsekunden-Kontrolle und Achtsekunden-Abnahme entfallen.
- Frühere Zweissekunden-Pakete gelten nicht mehr als native Startkonfiguration.
- Geräteschlüssel und HTTPS aus GreenMind verwenden.
- Vorschau im RAM halten; keine Livepunkte in TimescaleDB schreiben.
- Pro Sensor einen begrenzten Ringpuffer für drei Minuten vorsehen.
- Gesamtzahl Sensoren, Zuschauer und Speicherbedarf zusätzlich begrenzen.
- Serverneustart leert Vorschau; Rohaufzeichnung bleibt davon unabhängig.
- Fenster **30 Sekunden**, **1 Minute** und **3 Minuten** anbieten.
- Fehlende Pufferhistorie als unvollständiges Livefenster kennzeichnen.
- Rail-Prozent pro Sensor neben der Kurve anzeigen.
- Als Startkonfiguration ein gleitendes zehnsekündiges Rail-Messfenster verwenden.
- Rail-Anzeige während Live spätestens sekündlich mit aktuellem Zeitbezug erneuern.
- Messfenster und Aktualisierungsintervall sind unterschiedliche Grössen.
- Rail-Pakete enthalten Rohsamplezahl, gesättigte Samplezahl und Fensterzeitbezug.
- Veraltete Rail-Werte sichtbar kennzeichnen; fehlende Werte niemals als null darstellen.
- Hohe Rail-Anteile trotz plausibler Mittelwertkurve deutlich hervorheben.
- Einen **Mark-Knopf** für Ereignisse im Livefenster ergänzen.
- Markierungen an Sensor, Sitzung, Aufnahmezeit und Samplebezug binden.
- Marker-Sichtbarkeit, Haltbarkeit und Exportbedarf vor Implementierung konkretisieren.
- Mehrere Zuschauer erhalten unabhängige, verlängerbare Anzeigefreigaben.
- Freigaben gelten 30 Sekunden; Verlängerung erfolgt alle zehn.
- Letzter Zuschauer beendet Live; Freigabeablauf stoppt spätestens Übertragung.
- Vorschaupuffer begrenzen; Rohuploads besitzen Vorrang.
- Vorschauüberlastung darf weder Rohverluste noch zusätzliche Aufnahmefilter verursachen.
- Alte Firmware zeigt einen verständlichen Aktualisierungshinweis.

### 10. Aufnahmezeit und Vollständigkeit darstellen

- Aufnahmezeit aus Zeitanker, Sitzung und Sampleindex bestimmen.
- Hashwerte zur Integritätsprüfung und Zuordnung verwenden.
- Empfangszeit getrennt von Aufnahmezeit behandeln.
- Vorhandene Zeitrekonstruktion und Exportbenennung wiederverwenden.
- Diagrammtitel zeigt Aufnahmezeit und Zeitzone.
- Exportdateiname verwendet eindeutigen UTC-Zeitstempel.
- Fehlenden Zeitanker als ungesicherte Aufnahmezeit kennzeichnen.
- Datenlücken, laufende Aufnahmen und unvollständige Zeiträume sichtbar machen.
- Vollständigkeit und physische Zeitgenauigkeit getrennt bewerten.

### 11. Oberfläche vereinfachen

- Messansicht zeigt ausschliesslich „24 Stunden“ und „Live“.
- Jede neu geöffnete Messansicht startet mit „24 Stunden“.
- Hauptnavigation: Überblick, Pflanzen, Messungen, Verwaltung.
- Gateways, Zonen und Firmware unter Verwaltung bündeln.
- Gerätauswahl, Diagramm und Verbindungsstatus priorisieren.
- Live bietet zusätzlich 30 Sekunden, eine Minute und drei Minuten.
- Live zeigt Signalalter und Rail-Prozent je ausgewähltem Sensor.
- Rail-Messfenster, Aktualität und Übersteuerungswarnung sind erkennbar.
- Mark-Knopf bleibt im Livefenster direkt erreichbar.
- Übersteuerung als Messproblem erklären; keine Stressdiagnose ableiten.
- Dateilisten durch „Exportieren“ mit Geräte- und Zeitraumwahl ersetzen.
- Technische Details einklappen; historische Daten bleiben zugänglich.
- Der einfache Live-Mark-Knopf gehört jetzt zum Umfang.
- Weitergehende Unterrichtsfunktionen bleiben ausserhalb dieses Umfangs.

### 12. Vier Sprachen vervollständigen

- Deutsch, Englisch, Französisch und Italienisch vollständig unterstützen.
- Italienisch zu Sprachwahl und Routen ergänzen.
- Fest eingebaute Texte durch Übersetzungsschlüssel ersetzen.
- Englisch korrigieren; Begriffe über alle Sprachen vereinheitlichen.
- Login, Menüs, Meldungen und Exportdialog vollständig übersetzen.
- Livefenster, Rail-Hinweise und Mark-Knopf ebenfalls vollständig übersetzen.
- Sprachwechsel erhält Gerät, Seite und gewählten Zeitraum.

## Schnittstellen

Neue Direct-Schnittstellen unter `/api/v1/direct-ingest/devices/{id}`:

| Schnittstelle | Zweck |
|---|---|
| `PUT/DELETE /live-leases/{lease_id}` | Browser aktiviert, verlängert oder beendet Livebedarf. |
| `GET /control` | Firmware erhält verbleibende Livegültigkeit. |
| `POST /live-preview` | Gerät liefert Vorschau, Samplebezug und Signalstatus. |
| `GET /live-preview` | Berechtigter Browser erhält begrenzte Vorschau für 30/60/180 Sekunden. |

- Browserzugriff prüft Sitzung, Organisation und Zone.
- Änderungen mit Sitzungscookies prüfen zusätzlich die Herkunft.
- Livezustand bleibt im Direct-Dienst; historische Leser bleiben schreibgeschützt.
- Vorschaupakete enthalten Sitzung, Sequenz, Zeitbezug, Einheit und Filterprofil.
- Rail-Status ergänzt Rohsamplezähler, Sättigungszähler und Messfenster.
- Serverseitige Sequenz- oder Zeitcursor begrenzen wiederholte Antwortmengen.
- Marker-Schnittstelle nach Festlegung ihrer Sichtbarkeit und Haltbarkeit definieren.
- Marker-Schreibzugriffe prüfen ebenfalls Sitzung, Organisation, Zone und Herkunft.
- Mehrere Direct-Worker benötigen konsistenten Livezustand und eindeutige Gerätezuordnung.
- Flüchtiger gemeinsamer Speicher ist möglich; keine TimescaleDB-Livezeilen vorsehen.

- Vorschau bleibt flüchtig; Rohaufzeichnung bleibt massgebliche Datenquelle.

## Abnahme und Einführung

- S1: Ungültige Schlüssel verursachen begrenzten Aufwand trotz wachsender Gerätezahl.
- S2: Übergrössen scheitern vor JSON-Verarbeitung, auch ohne Längenangabe.
- S3/S6: Falscher Codetyp scheitert; parallele Einlösung bleibt einmalig.
- S4/S5: Zugänge bleiben eindeutig; Widerruf sperrt sofort Folgezugriffe.
- S7: Erfolg, Fehler, Abbruch und Ablauf hinterlassen keine Exportreste.
- Login funktioniert mit Neuladen, Sprachwechsel und abgelaufener Sitzung.
- Gateway-Entfernung sperrt Zugang und erhält historische Messungen.
- Live erfüllt unter stabilen Testbedingungen die Grenze unter drei Sekunden.
- Zeitlich referenzierte Blattberührung bis sichtbarer Kurvenänderung Ende-zu-Ende messen.
- Erste Livewerte und laufende Ereignisse getrennt prüfen.
- Jede definierte Testwiederholung muss die Latenzgrenze einhalten.
- Netzwerkbedingungen und vollständige Latenzverteilung im Abnahmeprotokoll festhalten.
- 12,5-Hz-Vorschau, sämtliche kurzen Fenster und Mark-Knopf prüfen.
- Hohe Rohwertsättigung bleibt trotz plausibler gefilterter Kurve sichtbar.
- Rail-Zähler gegen bekannte ADC-Grenzfälle und Rohsamplezahlen vergleichen.
- Fehlende, verspätete und unvollständige Vorschau korrekt kennzeichnen.
- Speicher bleibt auf 8-MB-Testhardware im Dauertest begrenzt.
- Rohuploads und Rohvollständigkeit vor beziehungsweise während Live vergleichen.
- Vorschau erzeugt keine TimescaleDB-Zeilen; Drei-Minuten-RAM-Grenze nachweisen.

- Mehrere Zuschauer, Tabwechsel und Verbindungsabbrüche funktionieren korrekt.
- Hardwaretests prüfen Abtastrate, Zeitabweichung, Grenzwerte und Datenverluste.
- Neustarts, Offlinepuffer und WAV-Kalibrierung werden geprüft.
- 24-Stunden-Dauertest umfasst Rohaufzeichnung und wiederholte Livewechsel.
- Mobilansicht und sämtliche vier Sprachen werden geprüft.
- Hauptagent bewertet alle Subagentenergebnisse und integriert bestandene Änderungen.
- Zuerst Backend-Kompatibilität, danach Frontend und einzelne Pilot-Firmware ausliefern.
- Vier Büroboxen bis zur fertigen Ersatzversion explizit ausschliessen.
- Pilotierung zunächst auf unabhängiger Testhardware durchführen.
- Büroboxwechsel erst nach nativer Abnahme und konkreter Produktionsfreigabe abstimmen.
- Übergangsbetrieb und Rückfall pro Bürobox vor dem Wechsel festhalten.
- Peter nach erfolgreicher Abnahme über die Wechselbereitschaft informieren.
- Abschaltung der Übergangsansicht erst nach abgestimmter erfolgreicher Übergabe.

- Zunächst Staging; dort Sicherheits-, Daten- und Hardwareprüfungen abschliessen.
- Produktion benötigt separate Freigabe des konkret geprüften Änderungsstands.
- Live bleibt abschaltbar; Rückfall erhält Rohaufzeichnung und Altdaten.
