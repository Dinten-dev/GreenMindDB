import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import ExportButton from '../ExportButton';
import de from '../../../../../../messages/de.json';
import en from '../../../../../../messages/en.json';
import fr from '../../../../../../messages/fr.json';
import it from '../../../../../../messages/it.json';
jest.unmock('next-intl');
jest.mock('../SignalChart', () => ({
  __esModule: true,
  default: () => <div data-testid="history-chart" />,
}));
const catalogs = { de, en, fr, it };
const devices = [
  { id: 'a', kind: 'direct' as const, name: 'Büro' },
  { id: 'b', kind: 'gateway' as const, name: 'Gewächshaus' },
];
const bounds = { from_dt: '2026-10-25T00:30:00.123Z', to_dt: '2026-10-25T01:30:00.456Z' };
const file = {
  segment_id: 'segment',
  revision: 2,
  run: 0,
  started_at: bounds.from_dt,
  duration_seconds: 600,
  sample_rate: 400,
};
beforeEach(() => {
  jest.clearAllMocks();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
  URL.createObjectURL = jest.fn(() => 'blob:test-export');
  URL.revokeObjectURL = jest.fn();
  jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  global.fetch = jest.fn(async (url) => ({
    ok: true,
    status: 200,
    headers: new Headers({ 'Content-Disposition': 'attachment; filename="capture.csv"' }),
    blob: async () => new Blob(['csv']),
    json: async () =>
      String(url).includes('/recordings')
        ? [file]
        : String(url).includes('/data?')
          ? [
              {
                kind: 'bio_signal',
                unit: 'mV',
                sensor_id: 'a',
                data: [{ timestamp: bounds.from_dt, value: 1 }],
              },
            ]
          : [],
  })) as jest.Mock;
});
afterEach(() => jest.restoreAllMocks());
function view(locale: keyof typeof catalogs = 'de') {
  return render(
    <NextIntlClientProvider locale={locale} messages={catalogs[locale]} timeZone="UTC">
      <ExportButton devices={devices} initialKey="direct:a" initialBounds={bounds} />
    </NextIntlClientProvider>
  );
}
function open() {
  fireEvent.click(screen.getByRole('button', { name: 'Exportieren' }));
  return screen.getByRole('dialog', { name: 'Exportieren' });
}
test('opens a modal with device and UTC-period selection, without preloading files', () => {
  view();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  const dialog = open();
  expect(within(dialog).getByRole('combobox', { name: 'Gerät' })).toHaveValue('direct:a');
  expect(
    within(dialog).getByRole('combobox', { name: 'Gerät' }).querySelectorAll('option')
  ).toHaveLength(2);
  expect(screen.getByText('Zeitzone: UTC')).toBeInTheDocument();
  expect(global.fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Schliessen' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
test('exports precisely the chart UTC bounds including milliseconds', async () => {
  view();
  open();
  fireEvent.click(screen.getByRole('button', { name: 'Herunterladen' }));
  await screen.findByText('Download gestartet.');
  const url = new URL(jest.mocked(global.fetch).mock.calls[0][0] as string, 'https://local.test');
  expect(url.pathname).toBe('/api/v1/visualization/direct/a/export');
  expect(url.searchParams.get('from_dt')).toBe(bounds.from_dt);
  expect(url.searchParams.get('to_dt')).toBe(bounds.to_dt);
  expect(global.fetch).toHaveBeenCalledWith(expect.any(String), { credentials: 'include' });
  expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
});
test('selects original recordings only after an explicit request with the same bounds', async () => {
  view();
  open();
  fireEvent.change(screen.getByRole('combobox', { name: 'Format' }), { target: { value: 'wav' } });
  expect(global.fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Originalaufnahmen auswählen' }));
  const link = await screen.findByRole('link', { name: /WAV herunterladen/ });
  expect(link).toHaveAttribute('href', '/api/v1/visualization/direct/a/wav/segment/2/0');
  const url = new URL(jest.mocked(global.fetch).mock.calls[0][0] as string, 'https://local.test');
  expect(url.searchParams.get('from_dt')).toBe(bounds.from_dt);
  expect(url.searchParams.get('to_dt')).toBe(bounds.to_dt);
  fireEvent.change(screen.getByRole('combobox', { name: 'Gerät' }), {
    target: { value: 'gateway:b' },
  });
  expect(screen.queryByRole('link', { name: /WAV herunterladen/ })).not.toBeInTheDocument();
});
test('loads an older historical chart without changing the main measurement mode', async () => {
  view();
  open();
  fireEvent.change(screen.getByLabelText('Von'), { target: { value: '2020-01-01T10:00:00.000' } });
  fireEvent.click(screen.getByRole('button', { name: 'Verlauf anzeigen' }));
  await screen.findByTestId('history-chart');
  const call = jest
    .mocked(global.fetch)
    .mock.calls.find(([path]) => String(path).includes('/data?'))!;
  expect(new URL(String(call[0]), 'https://local.test').searchParams.get('from_dt')).toBe(
    '2020-01-01T10:00:00.000Z'
  );
});
test('rejects an empty or reversed period before any request', () => {
  view();
  open();
  fireEvent.change(screen.getByLabelText('Bis'), { target: { value: '2026-10-24T00:00:00.000' } });
  expect(screen.getByRole('button', { name: 'Herunterladen' })).toBeDisabled();
  expect(screen.getByRole('alert')).toBeInTheDocument();
  expect(global.fetch).not.toHaveBeenCalled();
});
test('aborts a pending historical read on close', async () => {
  global.fetch = jest.fn(() => new Promise(() => {})) as jest.Mock;
  view();
  open();
  fireEvent.click(screen.getByRole('button', { name: 'Verlauf anzeigen' }));
  await waitFor(() => expect(global.fetch).toHaveBeenCalled());
  const signal = jest.mocked(global.fetch).mock.calls[0][1]?.signal;
  fireEvent.click(screen.getByRole('button', { name: 'Schliessen' }));
  expect(signal?.aborted).toBe(true);
});
test('does not expose private server errors or stale results after a failed read', async () => {
  global.fetch = jest.fn(async () => ({
    ok: false,
    status: 403,
    json: async () => ({ detail: 'private tenant data' }),
  })) as jest.Mock;
  view();
  open();
  fireEvent.click(screen.getByRole('button', { name: 'Verlauf anzeigen' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Exportdaten konnten nicht geladen werden'
  );
  expect(screen.queryByText(/private tenant/)).not.toBeInTheDocument();
});
test.each([
  ['de', 'Exportieren', 'Gerät'],
  ['en', 'Export', 'Device'],
  ['fr', 'Exporter', 'Appareil'],
  ['it', 'Esporta', 'Dispositivo'],
] as const)('translates the export flow into %s', (locale, label, deviceLabel) => {
  view(locale);
  fireEvent.click(screen.getByRole('button', { name: label }));
  expect(screen.getByRole('dialog', { name: label })).toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: deviceLabel })).toBeInTheDocument();
});
