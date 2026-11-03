import React from 'react';
import { render, screen } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import RecordingCompletenessBadge from '@/app/[locale]/app/sensors/RecordingCompletenessBadge';
import de from '../../../messages/de.json';
import en from '../../../messages/en.json';
import fr from '../../../messages/fr.json';
import italian from '../../../messages/it.json';
jest.unmock('next-intl');
const cases = [
  { locale: 'de', messages: de, label: 'Teilstück oder Datenlücke', duration: '12,5' },
  { locale: 'en', messages: en, label: 'Partial recording or gap', duration: '12.5' },
  { locale: 'fr', messages: fr, label: 'Enregistrement partiel ou lacune', duration: '12,5' },
  { locale: 'it', messages: italian, label: 'Registrazione parziale o lacuna', duration: '12,5' },
];
it.each(cases)(
  'localizes recording diagnostics and numeric display in $locale',
  ({ locale, messages, label, duration }) => {
    render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <RecordingCompletenessBadge
          completeness={{
            status: 'short',
            received_seconds: 12.5,
            expected_seconds: 600,
            coverage_ratio: 12.5 / 600,
            basis: 'verified_manifest',
          }}
        />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(new RegExp(label))).toHaveTextContent(duration);
  }
);
