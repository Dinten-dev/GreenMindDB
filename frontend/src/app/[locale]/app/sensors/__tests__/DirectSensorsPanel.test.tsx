import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import DirectSensorsPanel from '../DirectSensorsPanel';

jest.mock('../SignalChart', () => ({
  __esModule: true,
  default: ({ series, showPeaks }: { series: { data: unknown[] }; showPeaks: boolean }) => (
    <div data-testid="signal-chart" data-show-peaks={showPeaks}>
      {series.data.length} Punkte
    </div>
  ),
}));
const device = {
  id: 'direct-a',
  hardware_id: '14:C1:9F:D9:42:9C',
  status: 'online',
  last_seen: '2026-09-19T14:50:00Z',
};
const points = [{ timestamp: '2026-09-19T14:49:00Z', value: 1000 }];
afterEach(() => {
  jest.restoreAllMocks();
  jest.useRealTimers();
});

test('loads Direct measurements only when expanded and exposes authenticated WAV/CSV paths', async () => {
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
  const toggle = await screen.findByRole('button', { name: /Biolingo/ });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(global.fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(toggle);
  expect(await screen.findByTestId('signal-chart')).toHaveTextContent('1 Punkte');
  const exportUrl = screen.getByRole('link', { name: 'CSV herunterladen' }).getAttribute('href');
  expect(exportUrl).toContain('/api/v1/visualization/direct/direct-a/export?range=24h');
  expect(new URL(exportUrl!, 'http://localhost').searchParams.get('from_dt')).toBeTruthy();
  expect(new URL(exportUrl!, 'http://localhost').searchParams.get('to_dt')).toBeTruthy();
  expect(screen.getByRole('link', { name: 'WAV herunterladen', hidden: true })).toHaveAttribute(
    'href',
    '/api/v1/visualization/direct/direct-a/wav/segment-a/4/0'
  );
  fireEvent.click(screen.getByRole('button', { name: 'Live · 5 Min.' }));
  await waitFor(() =>
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringMatching(
        /^\/api\/v1\/visualization\/direct\/direct-a\/data\?range=5m&from_dt=.*&resolution=1$/
      ),
      expect.objectContaining({ cache: 'no-store' })
    )
  );
  expect(await screen.findByTestId('signal-chart')).toHaveAttribute('data-show-peaks', 'true');
  fireEvent.click(screen.getByRole('button', { name: '1 Stunde' }));
  await waitFor(() =>
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringMatching(
        /^\/api\/v1\/visualization\/direct\/direct-a\/data\?range=1h&from_dt=.*&resolution=1$/
      ),
      expect.objectContaining({ cache: 'no-store' })
    )
  );
  expect(await screen.findByTestId('signal-chart')).toHaveAttribute('data-show-peaks', 'true');
  expect(screen.getByRole('link', { name: 'CSV herunterladen' })).toHaveAttribute(
    'href',
    expect.stringMatching(
      /^\/api\/v1\/visualization\/direct\/direct-a\/export\?range=1h&from_dt=.*&resolution=1$/
    )
  );
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByTestId('signal-chart')).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'CSV herunterladen' })).not.toBeInTheDocument();
});

test('shows an explicit data error instead of hiding a device with failing chart requests', async () => {
  global.fetch = jest.fn(async (url) => ({
    ok: String(url).endsWith('/devices'),
    json: async () => [device],
  })) as jest.Mock;
  render(<DirectSensorsPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Biolingo/ }));
  expect(await screen.findByText(/Messdaten konnten nicht aktualisiert werden/)).toHaveAttribute(
    'role',
    'alert'
  );
  expect(screen.queryByText(/In diesem Zeitraum sind noch keine/)).not.toBeInTheDocument();
  expect(screen.getByText('Empfängt Daten')).toBeInTheDocument();
});

const signal = [{ sensor_id: device.id, kind: 'bio_signal_ch1', unit: 'mV', data: points }];

test('shows the curve when the recordings list fails', async () => {
  global.fetch = jest.fn(async (url) => ({
    ok: !String(url).includes('/recordings'),
    status: String(url).includes('/recordings') ? 503 : 200,
    json: async () => (String(url).endsWith('/devices') ? [device] : signal),
  })) as jest.Mock;
  render(<DirectSensorsPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Biolingo/ }));
  expect(await screen.findByTestId('signal-chart')).toHaveTextContent('1 Punkte');
  expect(screen.getByRole('alert')).toHaveTextContent('Aufnahmeliste');
  expect(screen.queryByText(/Noch keine abgeschlossene Aufnahme/)).not.toBeInTheDocument();
});

test('does not wait for a stalled recordings list before displaying the curve', async () => {
  jest.useFakeTimers();
  global.fetch = jest.fn((url) =>
    String(url).includes('/recordings')
      ? new Promise(() => {})
      : Promise.resolve({
          ok: true,
          json: async () => (String(url).endsWith('/devices') ? [device] : signal),
        })
  ) as jest.Mock;
  render(<DirectSensorsPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Biolingo/ }));
  expect(await screen.findByTestId('signal-chart')).toHaveTextContent('1 Punkte');
  await act(async () => jest.advanceTimersByTimeAsync(10000));
  expect(
    (global.fetch as jest.Mock).mock.calls.filter(([url]) => String(url).includes('/data?'))
  ).toHaveLength(2);
  expect(
    (global.fetch as jest.Mock).mock.calls.filter(([url]) => String(url).includes('/recordings'))
  ).toHaveLength(1);
});

test('retains the open curve when device status temporarily fails', async () => {
  jest.useFakeTimers();
  let statusFails = false;
  global.fetch = jest.fn(async (url) => ({
    ok: !(String(url).endsWith('/devices') && statusFails),
    status: String(url).endsWith('/devices') && statusFails ? 503 : 200,
    json: async () =>
      String(url).endsWith('/devices')
        ? [device]
        : String(url).includes('/recordings')
          ? []
          : signal,
  })) as jest.Mock;
  render(<DirectSensorsPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Biolingo/ }));
  expect(await screen.findByTestId('signal-chart')).toHaveTextContent('1 Punkte');
  statusFails = true;
  await act(async () => jest.advanceTimersByTimeAsync(10000));
  expect(screen.getByTestId('signal-chart')).toHaveTextContent('1 Punkte');
  expect(screen.getByText('Status unklar')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Biolingo/ })).toHaveAttribute('aria-expanded', 'true');
});

test('removes cached device data after authorization is rejected', async () => {
  jest.useFakeTimers();
  let revoked = false;
  global.fetch = jest.fn(async (url) => ({
    ok: !revoked,
    status: revoked ? 403 : 200,
    json: async () =>
      String(url).endsWith('/devices')
        ? [device]
        : String(url).includes('/recordings')
          ? []
          : signal,
  })) as jest.Mock;
  render(<DirectSensorsPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Biolingo/ }));
  expect(await screen.findByTestId('signal-chart')).toBeInTheDocument();
  revoked = true;
  await act(async () => jest.advanceTimersByTimeAsync(10000));
  expect(screen.queryByTestId('signal-chart')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Biolingo/ })).not.toBeInTheDocument();
});
