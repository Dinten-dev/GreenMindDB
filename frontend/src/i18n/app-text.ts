'use client';

import { useMemo } from 'react';
import { useLocale, useTranslations } from 'next-intl';
import { ApiConnectionError, ApiError } from '@/lib/api';
import messageKeys from './app-message-keys.json';

const keys: Record<string, string> = messageKeys;
const statuses: Record<string, string> = {
  online: 'Online',
  offline: 'Offline',
  disconnected: 'Getrennt',
  active: 'Aktiv',
  disabled: 'Deaktiviert',
  inactive: 'Inaktiv',
  member: 'Mitglied',
  owner: 'Eigentümer',
  admin: 'Administrator',
  pending: 'Ausstehend',
  queued: 'In Warteschlange',
  verifying: 'Wird geprüft',
  running: 'Läuft',
  copying: 'Wird kopiert',
  completed: 'Abgeschlossen',
  complete: 'Abgeschlossen',
  failed: 'Fehlgeschlagen',
  cancelled: 'Abgebrochen',
  canceled: 'Abgebrochen',
  expired: 'Abgelaufen',
  not_configured: 'Nicht eingerichtet',
  unknown: 'Unbekannt',
  paused: 'Pausiert',
  idle: 'Bereit',
  ready: 'Bereit',
  waiting: 'Wartet',
  downloading: 'Wird heruntergeladen',
  downloaded: 'Heruntergeladen',
  pending_window: 'Wartet auf Updatefenster',
  applying: 'Wird angewendet',
  applied: 'Angewendet',
  disk_insufficient: 'Speicher reicht nicht',
  none: 'Keine',
  success: 'Erfolgreich',
  failure: 'Fehlgeschlagen',
  hash_mismatch: 'Hash Mismatch',
  rollback: 'Rollback',
  incompatible: 'Inkompatibel',
  signed: 'Signiert',
  unsigned: 'Nicht signiert',
  invalid: 'Ungültig',
  ok: 'In Ordnung',
  low: 'Knapp',
  critical: 'Kritisch',
  requested: 'Angefragt',
  invited: 'Eingeladen',
  accepted: 'Angenommen',
  rejected: 'Abgelehnt',
  sending: 'Wird versendet',
  sent: 'Versendet',
  not_sent: 'Nicht versendet',
  greenhouse: 'Gewächshaus',
  field: 'Freiland',
  vertical_farm: 'Vertikale Farm',
  orchard: 'Obstgarten',
};

/** Translate fixed UI messages; never pass account names, filenames or data values. */
export function useAppText() {
  const translate = useTranslations('App');
  const locale = useLocale();
  return useMemo(() => {
    const text = (source: string, values?: Record<string, string | number | Date>) => {
      const key = keys[source];
      return key ? translate(key, values) : source;
    };
    return Object.assign(text, {
      locale,
      status: (value: string | null | undefined) =>
        text(statuses[value ?? 'unknown'] ?? 'Unbekannt'),
      error: (error: unknown) => {
        if (error instanceof ApiConnectionError)
          return text('Verbindung nicht verfügbar. Bitte erneut versuchen.');
        if (error instanceof ApiError) {
          const messages: Record<number, string> = {
            401: 'Sitzung abgelaufen. Bitte erneut anmelden.',
            403: 'Keine Berechtigung für diesen Vorgang.',
            404: 'Nicht gefunden. Bitte die Ansicht neu laden.',
            409: 'Die Daten haben sich geändert. Bitte neu laden.',
            422: 'Bitte die Eingaben prüfen.',
            429: 'Zu viele Versuche. Bitte später erneut versuchen.',
          };
          if (messages[error.status]) return text(messages[error.status]);
        }
        if (error instanceof Error && keys[error.message]) return text(error.message);
        return text('Vorgang fehlgeschlagen. Bitte erneut versuchen.');
      },
    });
  }, [translate, locale]);
}
