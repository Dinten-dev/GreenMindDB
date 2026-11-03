import { act, fireEvent, render, screen, within } from '@testing-library/react';
import DirectSensorsPanel from '../DirectSensorsPanel';
import { preserveMeasurementLocale } from '@/lib/measurement-view';

jest.mock('../SignalChart', () => ({
  __esModule: true,
  default: ({ series }: { series: { data: unknown[] } }) => (
    <div data-testid="signal-chart">{series.data.length} Punkte</div>
  ),
}));
jest.mock('../DirectLiveView', () => ({
  __esModule: true,
  default: () => <div data-testid="direct-live">Live</div>,
}));
const device = {
  id: 'direct-a',
  hardware_id: '14:C1:9F:D9:42:9C',
  status: 'online',
  last_seen: '2026-09-19T14:50:00Z',
};
const signal = [
  {
    sensor_id: device.id,
    kind: 'bio_signal_ch1',
    unit: 'mV',
    data: [{ timestamp: '2026-09-19T14:49:00Z', value: 1000 }],
  },
];
beforeEach(() => {
  window.history.replaceState({}, '', '/de/app/sensors');
  sessionStorage.clear();
  global.fetch = jest.fn(async (url) => ({
    ok: true,
    status: 200,
    json: async () => (String(url).endsWith('/devices') ? [device] : signal),
  })) as jest.Mock;
});
afterEach(() => {
  jest.restoreAllMocks();
  jest.useRealTimers();
});

test('loads only the curve on expansion and exposes exactly 24 hours and Live', async () => {
  render(<DirectSensorsPanel />);
  const toggle = await screen.findByRole('button', { name: /Biolingo/ });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(global.fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(toggle);
  expect(await screen.findByTestId('signal-chart')).toHaveTextContent('1 Punkte');
  const modes = within(screen.getByRole('group', { name: 'Zeitraum' }));
  expect(modes.getAllByRole('button')).toHaveLength(2);
  expect(modes.getByRole('button', { name: '24 Stunden' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.queryByRole('link', { name: 'WAV herunterladen' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Exportieren' })).toBeInTheDocument();
  expect(
    (global.fetch as jest.Mock).mock.calls.some(([url]) => String(url).includes('/recordings'))
  ).toBe(false);
  fireEvent.click(modes.getByRole('button', { name: 'Live' }));
  expect(screen.getByTestId('direct-live')).toBeInTheDocument();
  fireEvent.click(toggle);
  fireEvent.click(toggle);
  expect(await screen.findByTestId('signal-chart')).toBeInTheDocument();
  expect(screen.queryByTestId('direct-live')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '24 Stunden' })).toHaveAttribute(
    'aria-pressed',
    'true'
  );
});
test('shows a data failure explicitly while keeping the selected device visible', async () => {
  global.fetch = jest.fn(async (url) => ({
    ok: String(url).endsWith('/devices'),
    status: 503,
    json: async () => [device],
  })) as jest.Mock;
  render(<DirectSensorsPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Biolingo/ }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Messdaten konnten nicht aktualisiert werden'
  );
  expect(screen.getByText('Empfängt Daten')).toBeInTheDocument();
});
test('does not load a recordings list in the measurement view', async () => {
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
  expect(await screen.findByTestId('signal-chart')).toBeInTheDocument();
  expect(
    (global.fetch as jest.Mock).mock.calls.some(([url]) => String(url).includes('/recordings'))
  ).toBe(false);
});
test('retains the open curve when status is temporarily unavailable', async () => {
  jest.useFakeTimers();
  let statusFails = false;
  global.fetch = jest.fn(async (url) => ({
    ok: !(String(url).endsWith('/devices') && statusFails),
    status: 503,
    json: async () => (String(url).endsWith('/devices') ? [device] : signal),
  })) as jest.Mock;
  render(<DirectSensorsPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Biolingo/ }));
  await screen.findByTestId('signal-chart');
  statusFails = true;
  await act(async () => jest.advanceTimersByTimeAsync(10000));
  expect(screen.getByTestId('signal-chart')).toBeInTheDocument();
  expect(screen.getByText('Status unklar')).toBeInTheDocument();
});
test('clears the device and cached curve when access is rejected', async () => {
  jest.useFakeTimers();
  let denied = false;
  global.fetch = jest.fn(async (url) => ({
    ok: !denied,
    status: denied ? 403 : 200,
    json: async () => (String(url).endsWith('/devices') ? [device] : signal),
  })) as jest.Mock;
  render(<DirectSensorsPanel />);
  fireEvent.click(await screen.findByRole('button', { name: /Biolingo/ }));
  await screen.findByTestId('signal-chart');
  denied = true;
  await act(async () => jest.advanceTimersByTimeAsync(10000));
  expect(screen.queryByTestId('signal-chart')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Biolingo/ })).not.toBeInTheDocument();
});
test('a fresh URL restores the device but starts at 24 hours', async () => {
  window.history.replaceState({}, '', '/it/app/sensors?direct=direct-a&directRange=live');
  render(<DirectSensorsPanel />);
  expect(await screen.findByTestId('signal-chart')).toBeInTheDocument();
  expect(screen.queryByTestId('direct-live')).not.toBeInTheDocument();
  expect(new URLSearchParams(window.location.search).get('directRange')).toBeNull();
});
test('a locale switch restores Live once and a later opening defaults to 24 hours', async () => {
  window.history.replaceState(
    {},
    '',
    '/de/app/sensors?direct=direct-a&directRange=live&directLiveWindow=180'
  );
  preserveMeasurementLocale('/it/app/sensors');
  window.history.replaceState(
    {},
    '',
    '/it/app/sensors?direct=direct-a&directRange=live&directLiveWindow=180'
  );
  const page = render(<DirectSensorsPanel />);
  expect(await screen.findByTestId('direct-live')).toBeInTheDocument();
  page.unmount();
  render(<DirectSensorsPanel />);
  expect(await screen.findByTestId('signal-chart')).toBeInTheDocument();
  expect(screen.queryByTestId('direct-live')).not.toBeInTheDocument();
});
