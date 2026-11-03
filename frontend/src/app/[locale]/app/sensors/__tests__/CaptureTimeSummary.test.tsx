import { render, screen } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import CaptureTimeSummary, { RecordingTimeDetails } from '../CaptureTimeSummary';
import { formatCaptureTime, validCaptureStamp } from '@/lib/capture-time';
import type { SensorDataResponse } from '@/lib/api';
import de from '../../../../../../messages/de.json';
import en from '../../../../../../messages/en.json';
import fr from '../../../../../../messages/fr.json';
import it from '../../../../../../messages/it.json';
jest.unmock('next-intl');

const catalogs = { de, en, fr, it };
const series: SensorDataResponse = {
  sensor_id: 'test',
  source: 'direct',
  kind: 'bio_signal_ch1',
  unit: 'mV',
  data: [{ timestamp: '2026-10-25T00:30:00Z', value: 1 }],
  time_info: {
    anchor_status: 'available',
    basis: 'session_anchor_and_sample_index',
    physical_accuracy_verified: false,
    recorded_from: '2026-10-25T00:30:00Z',
    recorded_to: '2026-10-25T01:30:00Z',
    coverage_status: 'incomplete',
    coverage_ratio: 0.625,
    ongoing: true,
    gap_count: 2,
  },
};
function view(locale: keyof typeof catalogs = 'de', value = series) {
  return render(
    <NextIntlClientProvider locale={locale} messages={catalogs[locale]} timeZone="UTC">
      <CaptureTimeSummary series={value} />
    </NextIntlClientProvider>
  );
}
test('separates a running incomplete window from physical timing accuracy', () => {
  view();
  expect(screen.getByText('Aufnahme läuft')).toBeInTheDocument();
  expect(screen.getByText('Angezeigtes Zeitfenster unvollständig')).toBeInTheDocument();
  expect(screen.getByText(/62,5 %/)).toBeInTheDocument();
  expect(screen.getByText('Zeitgenauigkeit nicht unabhängig geprüft.')).toBeInTheDocument();
  expect(screen.getByText('Empfangszeit steht separat im Gerätestatus.')).toBeInTheDocument();
  expect(screen.queryByText('Vollständig')).not.toBeInTheDocument();
});
test('legacy or missing anchors stay uncertain and missing coverage is not zero', () => {
  view('de', { ...series, time_info: undefined });
  expect(screen.getByRole('status')).toHaveTextContent('Zeitanker fehlt');
  expect(screen.getByText('Datenabdeckung ungeprüft')).toBeInTheDocument();
  expect(screen.queryByText(/0 %/)).not.toBeInTheDocument();
});
test('UTC offsets distinguish the two repeated Zurich hours', () => {
  const a = formatCaptureTime('2026-10-25T00:30:00Z', 'de', 'Europe/Zurich');
  const b = formatCaptureTime('2026-10-25T01:30:00Z', 'de', 'Europe/Zurich');
  expect(a).toContain('02:30:00');
  expect(b).toContain('02:30:00');
  expect(a).not.toBe(b);
  expect(a).toContain('GMT+2');
  expect(b).toContain('GMT+1');
  expect(validCaptureStamp('2026-10-25T02:30:00')).toBe(false);
});
test.each([
  ['de', 'Aufnahmezeit und Datenabdeckung', 'Zeitgenauigkeit nicht unabhängig geprüft.'],
  ['en', 'Recording time and data coverage', 'Time accuracy has not been independently verified.'],
  [
    'fr',
    'Heure d’enregistrement et couverture des données',
    'La précision temporelle n’a pas été vérifiée indépendamment.',
  ],
  [
    'it',
    'Ora di registrazione e copertura dei dati',
    'La precisione temporale non è stata verificata indipendentemente.',
  ],
] as const)('renders capture time, timezone and uncertainty in %s', (locale, label, accuracy) => {
  view(locale);
  expect(screen.getByRole('region', { name: label })).toBeInTheDocument();
  expect(screen.getByText(accuracy)).toBeInTheDocument();
  expect(
    screen.queryByText('Aufnahmezeit ungesichert: Zeitanker fehlt oder ist unvollständig.')
  ).not.toBeInTheDocument();
});
test('preserves session, first sample and hash in collapsed details', () => {
  render(
    <NextIntlClientProvider locale="de" messages={de} timeZone="UTC">
      <RecordingTimeDetails
        time={{
          ...series.time_info!,
          session_id: 'session-123',
          first_frame: 32,
          sha256: 'a'.repeat(64),
        }}
      />
    </NextIntlClientProvider>
  );
  expect(screen.getByText('Sitzung: session-123')).toBeInTheDocument();
  expect(screen.getByText('Erstes Sample: 32')).toBeInTheDocument();
  expect(
    screen.getByText('Dateiintegrität und Zeitgenauigkeit sind getrennte Prüfungen.')
  ).toBeInTheDocument();
});
