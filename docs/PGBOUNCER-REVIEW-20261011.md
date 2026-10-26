# PgBouncer: veröffentlichter Reader und frische Abnahme

Stand: 11.10.2026. Löschung bleibt deaktiviert.

## Akzeptierter Release

`archive-reader-20261011-pgbouncer-v69`, Runtime
`/opt/greenmind/archive-preparation/20261011-pgbouncer-v69-authfix`.
Manifest SHA-256:
`278c50a05bee6882ce448e7bd35313bbc94f9ed2897fb8c81b7696418a41bae9`.

Vier Backend-Änderungen, Vorbereitung/Umschaltung, 60 Sekunden maximale
Kaltstartwartezeit, private AB:E8-Diagnose und zusätzlicher Authentifizierungsfix
wurden ausdrücklich genehmigt. Die Datenbankabfrage der privaten Anmeldung
läuft im vorhandenen Threadpool; Berechtigungsentscheidungen bleiben gleich.
Der vorherige synchrone Lookup blockierte bei parallelen Anfragen den Eventloop;
das Produktionsjournal bestätigte den QueuePool-Timeout.

Reader und Export auf dem Archivserver verwenden den privaten PgBouncer.
Der Kopierer verwendet bewusst seine ursprüngliche Konfiguration: Sein
Journal schützt die Source-Identität einschließlich Datenbankport. Die
Pool-Umstellung wurde vor dem Kopieren abgewiesen und ausschließlich dieses
Overlay zurückgenommen. Kein Journal wurde umgeschrieben, kein Kopierdienst
neu gestartet. Alle Originalruntimes und Beweise bleiben erhalten.

## Ressourcen und Schutzgrenzen

Source: PgBouncer 1.22.0, maximal drei Backend-Verbindungen, ausschließlich
alte, nicht öffentlich geroutete Verwaltungs-Fallbacks 8130/8131.
Archiv: PgBouncer 1.25.1, maximal vier Backend-Verbindungen über zwei
benannte Datenbanken und zwei bestehende private Leseidentitäten.
Keine Wildcard-Datenbanken, keine neuen SQL-Rollen oder erweiterten SQL-Rechte.
Zusätzliche Statistik-Anmeldungen erlauben ausschließlich Poolstatistiken.

Originalreserven bleiben erhalten: Source 640 MiB, Archiv 800 MiB.
Pooldienste haben 64 MiB und keinen Swap; Archivpool 10 % CPU.
Transaktionsgrenzen werden ausdrücklich mit `SET LOCAL` gesetzt.
Der reine Startup-Parameter-Test war fehlgeschlagen und bleibt als solcher
archiviert; der anschließende reale Transaktionsschutz bestand.
Keine Empfangsanwendung oder bestehende Source-Link-Verbindung geändert.

## Tatsächliche Abnahme

Aktivierung, Rücknahme und erneute Aktivierung bestanden vor Veröffentlichung
und öffentlichen Tests. Zwei frühere fehlgeschlagene Versuche wurden
zurückgenommen; ein weiterer Versuch brach vor Aktivierung ab.

- Öffentliche Authentifizierung, Zonenrechte und fremde Jobbesitz-Abweisung.
- Gateway-Zeitreihen 1 Stunde, 7 Tage, 30 Tage und 1 Jahr.
- CSV-/Anzeigeübereinstimmung einschließlich Direct-Minimum und -Maximum.
- Archiv-only Gateway-/Direct-WAV über ursprüngliche URLs: SHA-256 und Decode.
- Zwei frische ZIP-Aufträge: Fortschritt, Manifest, Größe, SHA-256, WAV-Decode.
- Alle fünf AB:E8-Fenster: 5 Minuten, 1 Stunde, 24 Stunden, 7 und 30 Tage.
  Je zwei parallele Daten-/Aufnahmeabfragen mit Browser-Zeitgrenzen: HTTP 200.
  Datenabfragen 0,44–0,85 Sekunden; 7-Tage-CSV 0,46 Sekunden.

Privater Reader-Pool: zwölf Transaktionen mit sechs parallelen Clients
bestanden; maximal ein Backend-PID pro Reader/Datenbankpaar.
Alle vier Rollen-/Datenbankpaare: zwölf serielle Transaktionen bestanden,
einschließlich Read-only, Isolation, Zeitlimits und Timeout/Rollback-Recovery.
Der zusätzliche sechsfach parallele Metadaten-Stresstest scheiterte am
unveränderten PostgreSQL-Rollenlimit zwei. Dieser Test ist kein PASS.
Produktive Metadatenprozesse bleiben durch die bestehende Lease serialisiert.

Lokale Authentifizierungstests: acht bestanden. Erweiterte Authentifizierungs-,
Visualisierungs- und Transaktionssuite: 68 bestanden, 22 externe Fixtures
übersprungen. Frühere fokussierte Suite: 80 bestanden, vier übersprungen.
Frische ZIP-Tests belegen keine Migration der drei älteren Downloadaufträge.
GitHub: Diese Runtime-Änderungen wurden nicht gepusht; die lokale Arbeitskopie
enthält umfangreiche fremde Änderungen und GitHub-main fehlt Archiv-Vorcode.

## Empfangskontinuität

Alle acht geschützten Originaldienste behielten Images, Startzeiten und
Neustartzähler. Source-Nginx blieb unverändert, SHA-256:
`29f29402f77d7f8eadb93825c924ba3c03af9e089ef009d1903def6e1a93348a`.

Begrenztes Empfangsfenster 11.10.2026, 15:37:51–15:47:51 UTC:

| Empfang | Erfolgreiche Antworten | HTTP 5xx |
|---|---:|---:|
| Gateway-Messungen | 1345 | 0 |
| Gateway-WAV | 17 | 0 |
| Direct-Chunks | 2995 | 0 |

Dieser Zeitraum beweist keine durchgehend verlustlose Sensorübertragung.
Source-Last kann weiter Schutzpausen auslösen; Reserven wurden nicht gesenkt.

## Frische 24-Stunden-Beobachtung

Beide Timer wurden gestartet und anschließend als aktiv ausgelesen:
`greenmind-archive-observation-accepted-20261011-pgbouncer-v69.timer`.

Source-Start: 11.10.2026 15:57:47.769758 UTC.
Archiv-Start: 11.10.2026 16:01:36.106108 UTC.
Beide ersten Stichproben bestanden. Paarlauf frühestens vollständig:
12.10.2026 18:01:36 Schweizer Zeit. Einmalige Nur-Lese-Auswertung:
12.10.2026 18:15 Schweizer Zeit.

Source-Evidenz:
`/mnt/HC_Volume_106755700/greenmind-delete-preparation/observation-accepted-20261011-pgbouncer-v69`.
Archiv-Evidenz:
`/var/lib/greenmind/archive-accepted-observation-pgbouncer-v69`.
Mindestens 280 Stichproben, maximal 600 Sekunden Abstand, keine Fehler,
Schutzpausen, geschützten Dienständerungen oder Empfangs-HTTP-5xx.
Empfangsfortschritt und vollständige begrenzte Logabdeckung werden geprüft.
Alte v59-Timer wurden beendet; ihre Evidenz ersetzt diese Abnahme nicht.

Der Start ist noch keine bestandene 24-Stunden-Abnahme und keine
Löschbereitschaft. Finaler Snapshot, Wiederherstellungsbeweise und separate
exakte Pilotfreigabe bleiben eigenständige Bedingungen.

## Rücknahme und Datenbestand

Die geprüften Umschalter verwenden erhaltene Originalkonfigurationen und
hashgeprüfte Runtimes. Nur der Archiv-Reader darf dabei neu gestartet werden;
kein Receiver. Der unabhängige begrenzte Rücknahmetimer berücksichtigt den
privaten akzeptierten Release-Nachweis. Source-Rücknahme betrifft nur die
beiden alten Verwaltungs-Fallbacks.

Keine WAV-, Snapshot-, Katalog- oder Journaldaten entfernt. Keine Deletion-,
Retention-, Pruning-, Lifecycle- oder Versionspolicy aktiviert.
