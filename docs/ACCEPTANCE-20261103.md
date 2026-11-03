# Gesamtabnahme und Einführungsvorbereitung — Schritt 13

Stand: 03.11.2026. Der Benutzer hat Schritt 13 als Gesamtabnahme und
Einführungsvorbereitung bestätigt. Der ursprüngliche Plan hatte zwölf Schritte;
dieses Dokument konkretisiert dessen anschliessenden Abschnitt „Abnahme und
Einführung“.

**Softwarekandidat zur Prüfung; keine Gesamt-, Staging- oder Produktionsfreigabe.**
Die nachstehenden offenen Prüfungen verhindern eine Einführung. Ein grüner
Softwarelauf ersetzt keine physische Abnahme.

## Geprüfte Ausgangsstände

| Bestandteil | Exakter Stand / Nachweis |
| --- | --- |
| Veröffentlichungszweig, Schritt 12 | `fce8d6d76d0392fd387754a0eaa4e7d98d31288b`, [PR #37](https://github.com/Dinten-dev/GreenMindDB/pull/37), [CI](https://github.com/Dinten-dev/GreenMindDB/actions/runs/41686388262) |
| Aktueller Receiver, Schritt 12 | `760c666b4fdc5f8a822d7b083d824cbeb74c27b3`, [PR #38](https://github.com/Dinten-dev/GreenMindDB/pull/38), [CI](https://github.com/Dinten-dev/GreenMindDB/actions/runs/41686397641) |
| Direct-Firmware, Schritt 9 | `06be31fb61e3b1c5e6565389e2671b73cbed4ccd`, [PR #7](https://github.com/Dinten-dev/GreenMindArdu/pull/7), [Native-/ESP32-CI](https://github.com/Dinten-dev/GreenMindArdu/actions/runs/41663971800) |

Die beiden Datenbankzweige sind getrennte Kandidaten. Der aktuelle Receiver
enthält zusätzliche Archiv-, Plattform- und Berechtigungsfunktionen; ihn nicht
durch den historischen Veröffentlichungszweig ersetzen. Endgültige Prüfungen
müssen den tatsächlich gewählten Commit betreffen. Schritt-13-CI läuft auf den
neuen Branches; ihr Ergebnis und vollständiger Head stehen im jeweiligen PR.

## Software-Prüfmatrix

„Automatisiert“ bezeichnet die unten genannten Verträge in isolierten Tests,
nicht beobachtetes Verhalten auf Production. Pfade ohne Präfix beziehen sich
auf `backend/tests/`. Die optionale Exportdienst-Prüfung S7 gehört ausschliesslich
zum aktuellen Receiver; dieser Dienst existiert im Veröffentlichungszweig nicht.

| Anforderung | Reproduzierbarer Nachweis | Grenze / offene Abnahme |
| --- | --- | --- |
| S1: begrenzter Aufwand ungültiger Schlüssel | `backend/tests/test_public_api_bounds.py`, `test_gateway_key_migration_postgres.py` | Reale Bestandsmigration nur separat geprüft durchführen. |
| S2: Bodygrenze vor JSON, auch gestreamt | `test_public_api_bounds.py` | Reale Proxykonfiguration zusätzlich auf Staging prüfen. |
| S3/S6: typisierte, einmalige Einlösung | `test_typed_pairing.py`, `test_pairing_postgres.py` | Echte PostgreSQL-Konkurrenz; keine parallelen gültigen Einlösungen. |
| S4/S5: eindeutiger Zugang und sofortiger Widerruf | `test_public_access_revocation.py`, `test_observation_access_postgres.py`, `test_public_observe.py` | Aktive öffentliche Sitzung nach Widerruf erneut prüfen. |
| S7: temporäre Exportkopien entfernen | `tests/visualization/test_export_cleanup.py`, `test_workspace_exports.py` | Erfolg, Fehler, Abbruch, Ablauf; Original-WAVs bleiben erhalten. |
| Login, Sitzung und Rollen | `test_auth_router.py`, `test_invitations_sessions.py`, Frontend-Komponententests | Mobil-/Browserdurchsicht offen; aktuelle Anmeldung behält „Zugang anfragen“. |
| Gateway entfernen, Historie behalten | `test_gateway_removal.py`, `test_gateway_removal_postgres.py` | Reale Staging-Historie vor/nach Deaktivierung vergleichen. |
| Raw400 und unveränderte alte Gateway-Verträge | `tests/direct/test_raw400_postgres.py`, Firmware-Nativtests | 400-Hz-Direct und 380-Hz-Gateway getrennt halten. |
| Bedarfsfreigaben, Rail, Marker, RAM-Grenzen | `tests/direct/test_live*.py`, Firmware-Nativtests | Physische Latenz, 8-MiB-Speicher und 24 Stunden offen. |
| UTC-/Samplebezug und WAV-/CSV-Grenzen | `test_recording_time.py`, `test_workspace_exports.py`, entsprechende Visualisierungstests | Referenzsignal und Geräteuhr physisch prüfen. |
| Einfache Messansicht, vier Sprachen | Frontend-Tests, Build, `npm run test:routes` (20 HTTP-Prüfungen) | Sichtprüfung hinter Login, mobile Darstellung und Tastatur offen. |
| Receiver-/Archivkompatibilität | PostgreSQL-/Timescale-Jobs; aktueller Zweig zusätzlich Archiv-/Plattformtests | Keine Aussage über heutige Production-Backups oder Speicherreserven. |
| Proxykontinuität und Rückfall | `test_isolated_release.py`, `test_gateway_continuity_guard.py`, `test_release_proxy_integration.py` | HTTP-Rehearsal synthetisch; physischer Parallelbetrieb auf Staging offen. |
| Abnahmenachweis vor Übertragung prüfen | `test_release_acceptance_cli.py` | Prüft vorhandene Metadaten, erzeugt keine Freigabe. |

Jeder Schritt-13-Zweig führt Frontend-Prüfungen einschliesslich Produktionsbuild,
HTTP-Sprachrouten, Typprüfung, Lint und Komponentenprüfungen aus. PostgreSQL und
Timescale laufen in GitHub CI als isolierte Testdienste. Für den aktuellen
Receiver kommen die bisher nicht im Schritt-12-Sammellauf enthaltenen
öffentlichen Schutzprüfungen, Releaseprüfungen und PostgreSQL-Zugangsrennen hinzu.
Abhängigkeitsprüfungen bleiben Bestandteil der bestehenden Jobs.

## Vorliegender USB-Versuch: ausdrücklich nicht bestanden

Der separate USB-Testsensor erhielt den Schritt-9-Quellstand mit privatem
lokalem HTTPS-Ziel und eigenem Test-NVS-Namensraum. Das war kein unverändertes
Cloud-Binary. Das Gerät hat 16 MiB Flash; es ist kein 8-MiB-Nachweis.

| Beobachtung | Ergebnis |
| --- | --- |
| Vollständige Beobachtung | 87 Fenster, 870,809 Sekunden |
| Unveränderter Capture-Prüfer | `capture_criteria_met=false`, `release_authorized=false` |
| Gründe | Leeres Startfenster, zu kurze Beobachtung, Rate ausserhalb Toleranz beim Start |
| Separat ausgewiesene Fenster ab Boot +30 Sekunden | 85 Fenster, 850,798 Sekunden, 399,919–400,081 Hz |
| Rohdaten-ACKs / Livepakete | 0 / 0 |
| Physischer WAV-Rücklese-/Hashnachweis | Offen |
| Blattberührung bis Bildschirm / Uhrfehler | Offen |

Die verwaltete macOS-Firewall blockierte den lokalen Testempfänger. Die erlaubte
temporäre Freigabe konnte unter dem Verwaltungsprofil nicht ausgeführt werden;
es wurden keine Firewallregeln geändert. Der lokale Empfänger ist beendet.
Für die Fortsetzung braucht es einen administrativ erreichbaren Testempfänger
oder eine reguläre Freigabe des bestehenden lokalen Empfängers. Das
Verwaltungsprofil nicht umgehen. Testgerät bei Wiederaufnahme auf sein lokales
Testziel prüfen; Peters Büroboxen sind nicht dieses Testgerät.

Die getrennte Auswertung nach 30 Sekunden korrigiert den vollständigen
fehlgeschlagenen Befund nicht. Sie belegt weder Upload noch Live noch Dauerbetrieb.
Kennwörter, Schlüssel, private Zertifikate und unbereinigte Logs nicht in PRs,
Artefakte oder dieses Dokument aufnehmen.

## Offene Abnahme: ausführbare Prüffolge

Pro Durchführung: UTC-Zeit, Prüfer, vollständigen DB-/Firmware-Commit,
Backend-/Frontend-Image-Config-IDs, Firmware-Binary-SHA256, Buildprofil,
Flash/PSRAM, getrennte Testgeräte, Netzwerkbedingungen und bereinigte Belege
festhalten. Ungetestete, abgebrochene oder fehlgeschlagene Fälle bleiben offen.

1. **Erreichbarkeit und Parallelbetrieb.** Zuerst kompatibles Backend samt
   privatem Live-Eigentümer auf einer freigegebenen Staging-Umgebung vorbereiten.
   Bestehenden Gateway unverändert weiter empfangen lassen. Separates Direct-Gerät
   verbinden; bestätigte Raw-ACKs und rückgelesene WAVs vor, während und nach Live
   mit Sampleindex, Nutzdaten und Hash vergleichen. Kein Raw-ACK darf nur aus
   einer Liveantwort abgeleitet werden.
2. **Physisches Signal und Zeit.** Referenzsignal samt Zeitreferenz einspeisen.
   Rail-Niedrig-/Hochgrenzen und nicht gesättigte Werte prüfen. Raw-Anzahl,
   Rail-Anzahl und zehnsekündiges Messfenster gegen die Eingabe vergleichen;
   unvollständige Fenster dürfen keine scheinbar gültigen Nullprozent zeigen.
   Hohe Rail-Anteile müssen trotz plausibler Mittelwertkurve sichtbar bleiben.
3. **Berührungslatenz.** Unter dokumentiert stabilem WLAN fünf unabhängige
   Live-Starts und zwanzig zeitlich referenzierte Blattberührungen prüfen.
   Ersten sichtbaren Punkt und laufende Kurvenreaktion getrennt messen.
   Alle Einzelwerte, Minimum, Median, P95 und Maximum dokumentieren.
   Jeder Versuch muss unter 3,000 Sekunden bleiben; fehlende Reaktionen und
   ausgelassene Versuche bestehen nicht. Video-/Zeitreferenzbeleg aufbewahren.
4. **Vorschau und Zuschauer.** 12,5 Punkte pro Sekunde und Fenster 30/60/180 s
   vergleichen. Zwei berechtigte Zuschauer öffnen; einen Tab ausblenden,
   schliessen, abmelden und die Verbindung verlieren lassen. Nur dessen
   30-Sekunden-Freigabe entfällt. Ohne Zuschauer muss Live auslaufen, Raw
   weiterlaufen. Verzögerte/fehlende Vorschau bleibt erkennbar.
5. **Markierungen und Isolation.** Zwei berechtigte Zuschauer sehen dieselbe
   Markierung am referenzierten frischen Sample. Unberechtigte sehen sie nicht.
   Nach höchstens 180 Sekunden und nach Neustart ist sie entfernt.
   Kein Export und keine SQL-/Objektspeicherzeile für Vorschau oder Marker.
   Drei-Minuten-RAM-Grenze auch ohne neue Anfragen prüfen.
6. **Dauerbetrieb und Wiederanlauf.** Mindestens 24 zusammenhängende Stunden auf
   tatsächlicher 8-MiB-Testhardware mit realer WLAN/TLS-Last, kontinuierlichem Raw
   und wiederholtem Livewechsel messen. N16R8 separat prüfen. Vollständiges
   Diagnoseprotokoll mit dem vorhandenen Firmware-Werkzeug
   `tools/check_raw400_capture.py` auswerten: 400±0,5 Hz je Fenster,
   höchstens 625 µs Rasterabweichung, 1250 µs ADC-/Callback-Dauer,
   mindestens 64 KiB interner freier Heap, keine Erfassungs-/Transportverluste.
   Heaptrend und Puffergrenzen zusätzlich belegen. WLAN-Ausfall nur innerhalb
   gemessener Pufferkapazität prüfen, Neustart und anschliessenden WAV-Rücklese-
   und Hashvergleich protokollieren. Volle Puffer sind kein Vollständigkeitsbeleg.
7. **Vier Sprachen und Bedienung.** Deutsch/Englisch/Französisch/Italienisch auf
   Mobilgerät und Desktop durchgehen: Login/Neuladen/Ablauf, Überblick, Pflanzen,
   Messungen, Verwaltung, rollenabhängige Administration, QR-Bewertung,
   Exporterfolg/-fehler/-abbruch, Live/Rail/Mark. Neue Messansicht startet bei
   24 Stunden. Sprachwechsel erhält Gerät, Seite und explizites Livefenster.
   Fehlende Übersetzungen, abgeschnittene Texte, Fokus und Tastaturbedienung
   dokumentieren. Lokaler Browserzugriff ist derzeit in Work deaktiviert;
   Komponenten- und HTTP-Prüfungen ersetzen diese Sichtprüfung nicht.
8. **Staging-Rückfall.** Exakte Images und denselben Commit mit dem bestehenden
   [Releaseverfahren](../deploy/release/README.md) prüfen. Proxywechsel,
   fehlgeschlagene Aktivierung und Rückfall tatsächlich üben. Gateway-Empfang,
   Direct-Raw und historische Zugriffe vergleichen; laufende Receiver nicht
   neu starten. Keine SQL-Retention, Archivlöschung oder Pruning aktivieren.

## Einführung erst nach diesen Nachweisen

1. Einen der beiden Kandidaten nach Durchsicht auswählen. Die gestapelten PRs
   benötigen Integration in der passenden Historie; kein Schritt-13-Push integriert
   sie automatisch. Nach Integration den exakten neuen Commit erneut prüfen.
2. Das vorhandene manuelle Paketverfahren setzt erfolgreiche CI für genau diesen
   Commit und dessen Zugehörigkeit zu `main` oder `develop` voraus.
   Die aktuellen Draft-Branches erfüllen noch keine freigegebene Integration.
   Kein Serverbuild und kein automatischer Rollout durch diese Arbeit.
3. Erst mit vollständig bestandener Staging-Abnahme die bestehende
   `staging-acceptance.json` ausfüllen: exakter Commit, beide Image-Config-IDs,
   echte Beobachtungszeit, fünf tatsächlich bestandene Zusammenfassungen.
   Dieses Dokument und die Detailbelege gehören daneben. Die fünf booleschen
   Zusammenfassungen sind keine zusätzliche Messung. Der Nachweis verfällt nach
   sieben Tagen oder bei geändertem Commit/Image.
4. Mit `release.py check-acceptance` die vorhandenen Metadaten lokal prüfen.
   Kein sudo, Docker, nginx, Netzwerk oder Releaseverzeichnis erforderlich.
   Selbst Erfolg gibt `release_authorized: false` aus. Die Betriebsfreigabe des
   konkreten Pakets und Ziels bleibt ein eigener Schritt.
5. Zuerst freigegebenes Staging, dann einzeln freigegebene unabhängige
   Pilot-Hardware. Produktionswechsel separat am konkret geprüften Stand
   freigeben. Live-Rückfall deaktiviert Live und erhält Raw und Altdaten.
   400-Hz-Binaries niemals als frühere 380-Hz-Downloads veröffentlichen.
6. **Peters vier Büroboxen bleiben ausgeschlossen.** Vor einem späteren Wechsel
   alle vier Geräte eindeutig zuordnen, eigenen Build/Rückfall und Übergang pro
   Box dokumentieren und den Wechsel abstimmen. Bisher sind nur 94b8/94cc als
   Kennungsenden bekannt. Keine OTA-Kampagne und keine Abschaltung von GmLive.
   Erst nach erfolgreicher nativer Abnahme eine Übergabemeldung vorbereiten;
   Versand an Peter und Abschaltung benötigen ihren gesonderten Auftrag.

## Abschlussstatus

Schritt 13 liefert die nachvollziehbare Softwareprüfung, die Vorprüfung bestehender
Abnahmeunterlagen und diese konkrete Einführungsvorbereitung. Gesamtabnahme und
Einführung sind erst nach den offenen physischen, visuellen und Staging-Prüfungen
erreicht. Dieser Stand enthält keine dafür erfundenen Messwerte oder Freigaben.
