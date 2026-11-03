# Web-Login: Schritt 6

## Fehler und Verhalten

- Zwei Ausgangsfehler durch Regressionstests reproduziert.
- Login verlor das Sprachpräfix bei der Weiterleitung.
- Ältere Sitzungsabfragen überschrieben einen später erfolgreichen Login.
- Browserprüfung fand zusätzlich konkurrierende Weiterleitungen beim Abmelden.
- Absichtliches Abmelden führt jetzt zur lokalisierten Startseite.
- Login, Navigation und Zugangsschutz teilen einen AuthProvider.
- Nach Anmeldung bestätigt `/auth/me` das serverseitige Sitzungscookie.
- Neuladen, Seitenwechsel und Tabaktivierung prüfen die Sitzung erneut.
- Aktive Sitzungen werden zusätzlich minütlich bei sichtbarem Tab geprüft.
- Veraltete Antworten ändern keine neuere Anmeldung oder Abmeldung.
- Geschützte 401-Antworten im gemeinsamen Client beenden die Sitzungsanzeige.
- Rollenfehler 403 sowie öffentliche Loginfehler lösen keinen Logout aus.
- Netzwerk-/Dienstfehler bleiben wiederholbar, ohne behauptete ungültige Zugangsdaten.
- Verbindungsfehler beim Abmelden behalten den bisherigen Benutzerstatus.

## Oberfläche und Weiterleitungen

- Deutsch, Englisch, Französisch und Italienisch erhalten Loginmeldungen.
- Angemeldete Anwendung ebenfalls übersetzt: [I18N-APPLICATION.md](I18N-APPLICATION.md).
- Rücksprungziele erlauben ausschliesslich lokalisierte interne App-Pfade.
- Fremde Ursprünge, fremde Sprachen und Login-Schleifen werden verworfen.
- Sprachwechsel lokalisiert auch das geprüfte Rücksprungziel.
- Fehler unterscheiden Zugangsdaten, Sitzung, Netz, Dienst und Versuchslimit.
- Unbestätigte E-Mail-Adressen und deaktivierte Konten erhalten eigene Hinweise.
- Ladezustand verhindert doppelte Anmeldungen; Passwortanzeige ist umschaltbar.
- Bestehende kurze Passwörter werden beim Login nicht ausgeschlossen.

## Prüfung und Grenzen

- Veröffentlichungslinie: 91 Frontendtests, Lint, Format, Typprüfung und Build bestanden.
- Aktueller Receiver: 107 Frontendtests, Lint, Format, Typprüfung und Build bestanden.
- Bestehende Einladungsabläufe bleiben im aktuellen Receiver erhalten.
- Backend unverändert: 61 beziehungsweise 83 gezielte Auth-/Schutzprüfungen bestanden.
- Browser lokal: falsches Passwort, Login, Neuladen, Dienstunterbruch und Wiederholung.
- Browser lokal: Sprachwechsel samt Ziel und französischer Login geprüft.
- Mobile Abmeldung führt nach Serverbestätigung zur französischen Startseite.
- Browser verwendet ausschliesslich synthetische Konten und lokalen Cookie-Testdienst.
- HTTP-/Sitzungsschutz zusätzlich gegen die tatsächlichen Backends getestet.
- Produktion, echte Konten und produktive Proxykonfiguration bleiben ungeprüft.
- HttpOnly, Cookieattribute, Herkunftskontrollen und Sitzungswiderruf bleiben unverändert.
- Keine Tokens oder Benutzerdaten in JavaScript-Persistenz gespeichert.
- Verifizierungs- und Einladungstokens bleiben aus Navigation und Rücksprungzielen entfernt.
- Bestehende Aufzeichnungs-, Archiv- und Zeitfunktionen bleiben erhalten.

## Einführung

- Änderungen zunächst gemeinsam auf Staging bereitstellen und dort prüfen.
- Anmeldung, Reload, Ablauf, Rollenfehler und Ausfälle erneut durchspielen.
- Aktueller Receiver behält seine persistierten Sitzungen und Einladungspflicht.
- Veröffentlichungslinie erhält keine fremden Backend-/Migrationshistorien.
- Veröffentlichungslinie baut auf Schritt 4, Receiver auf Schritt 5.
- Produktionsfreigabe betrifft den konkret geprüften Stand und erfolgt separat.
- Kein Merge, Deployment, Originaldaten-Löschlauf oder OTA enthalten.
- Peters Büroboxen und GmLive bleiben unverändert.
