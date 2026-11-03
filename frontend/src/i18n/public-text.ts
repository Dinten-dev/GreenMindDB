'use client';

import { useMemo } from 'react';
import { useLocale, useTranslations } from 'next-intl';
import messageKeys from './public-message-keys.json';

const keys: Record<string, string> = messageKeys;
/** Translate fixed public copy only; names, identifiers and submitted values stay data. */
export function usePublicText() {
  const translate = useTranslations('Public');
  const locale = useLocale();
  return useMemo(() => {
    const text = (source: string, values?: Record<string, string | number | Date>) =>
      keys[source] ? translate(keys[source], values) : source;
    return Object.assign(text, {
      locale,
      error: (error: unknown) => {
        const status =
          typeof error === 'object' && error !== null && 'status' in error ? error.status : null;
        return text(
          status === 429
            ? 'Zu viele Anfragen. Bitte später erneut versuchen.'
            : status === 422
              ? 'Bitte die Eingaben prüfen.'
              : 'Senden fehlgeschlagen. Bitte später erneut versuchen.'
        );
      },
    });
  }, [translate, locale]);
}
