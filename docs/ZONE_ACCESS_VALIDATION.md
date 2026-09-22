# Lokale Prüfung vom 22. September 2026

Basis: develop 61cce4e. Noch kein neuer Commit, Push oder Server-Rollout.

| Prüfung | Ergebnis |
| --- | --- |
| Backend ohne integration-Markierung | 271 bestanden, 4 übersprungen, 37 abgewählt |
| PostgreSQL: echte Migration 0023 und Direct-Verlauf | 8 bestanden |
| Frontend Jest | 10 Testsuiten, 27 Tests bestanden |
| Frontend Produktionsbuild | bestanden |
| TypeScript | bestanden |
| ESLint ohne Warnungen | bestanden |
| Ruff Prüfung und Formatierung | bestanden |
| Git Diff Whitespace-Prüfung | bestanden |

Die 37 abgewählten Integrationstests wurden nicht als vollständige Integrationssuite ausgeführt. Die separat ausgewählten PostgreSQL-Tests verwenden einen lokalen Docker-Testserver, keine Produktionsdatenbank. Warnungen im Backend betreffen bestehende Bibliotheks-Abkündigungen.

Nachweise liegen unter [validation/zone-access-20260922](validation/zone-access-20260922).

Besonders geprüft: mehrere Zonenfreigaben, keine Freigaben, organisationenübergreifende Zuweisung, Zugriff über erratene URLs, WAV-Metadaten/-Download, Legacy-/Direct-Messwerte, Entzug bei offenen Live-Verbindungen und Sensorumzug. Gateway- und Direct-Uploads funktionieren in den lokalen Tests weiterhin, nachdem Dashboard-Zugänge entzogen wurden. SMS-Empfänger sind zonenbezogen. Die bestehende SMS-Prüfung erhält nun ausdrücklich eine verifizierte Benutzeridentität und Zonenfreigabe.

Nicht durchgeführt: Installation auf Staging/Production, Rollenabgleich der echten Gary-/Peter-Konten, öffentliche Browser-/Hardwareabnahme oder Lasttest der zusätzlichen Live-Berechtigungsabfragen. Vorgehen und Rücknahmegrenzen siehe [ZONE_ACCESS_REVIEW.md](ZONE_ACCESS_REVIEW.md).
