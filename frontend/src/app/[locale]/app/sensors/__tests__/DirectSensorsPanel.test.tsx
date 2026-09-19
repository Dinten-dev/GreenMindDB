import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import DirectSensorsPanel from '../DirectSensorsPanel';

jest.mock('../SignalChart', () => ({
  __esModule: true,
  default: ({ series }: { series: { data: unknown[] } }) => (
    <div data-testid="signal-chart">{series.data.length} Punkte</div>
  ),
}));
const device = {
  id: 'direct-a',
  hardware_id: '14:C1:9F:D9:42:9C',
  status: 'online',
  last_seen: '2026-09-19T14:50:00Z',
};
const points = [{ timestamp: '2026-09-19T14:49:00Z', value: 1000 }];
afterEach(() => jest.restoreAllMocks());

test('opens Direct measurements automatically and exposes authenticated WAV/CSV paths', async () => {
  global.fetch = jest.fn(async (url) => ({
    ok: true,
    json: async () =>
      String(url).endsWith('/devices')
        ? [device]
        : String(url).includes('/recordings')
          ? [
              {
                segment_id: 'segment-a',
                revision: 4,
                run: 0,
                started_at: '2026-09-19T14:40:00Z',
                duration_seconds: 600,
                sample_rate: 380,
              },
            ]
          : [
              {
                sensor_id: 'direct-a',
                kind: 'bio_signal_ch1',
                unit: 'mV',
                source: 'direct',
                data: points,
              },
            ],
  })) as jest.Mock;
  render(<DirectSensorsPanel />);
  expect(await screen.findByTestId('signal-chart')).toHaveTextContent('1 Punkte');
  expect(screen.getByRole('link', { name: 'CSV herunterladen' })).toHaveAttribute(
    'href',
    '/api/v1/visualization/direct/direct-a/export?range=24h'
  );
  expect(screen.getByRole('link', { name: 'WAV herunterladen', hidden: true })).toHaveAttribute(
    'href',
    '/api/v1/visualization/direct/direct-a/wav/segment-a/4/0'
  );
  fireEvent.click(screen.getByRole('button', { name: 'Live · 5 Min.' }));
  await waitFor(() =>
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/v1/visualization/direct/direct-a/data?range=5m',
      expect.objectContaining({ cache: 'no-store' })
    )
  );
});

test('shows an explicit data error instead of hiding a device with failing chart requests', async () => {
  global.fetch = jest.fn(async (url) => ({
    ok: String(url).endsWith('/devices'),
    json: async () => [device],
  })) as jest.Mock;
  render(<DirectSensorsPanel />);
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Messdaten konnten nicht aktualisiert werden'
  );
  expect(screen.getByText('Empfängt Daten')).toBeInTheDocument();
});
