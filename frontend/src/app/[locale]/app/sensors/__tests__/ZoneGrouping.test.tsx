import { preserveMeasurementLocale } from '@/lib/measurement-view';
import { apiGetSensorDataAdvanced, apiListSensors } from '@/lib/api';
import { fireEvent, render, screen, within, waitFor } from '@testing-library/react';
import SensorsPage from '../page';

jest.mock('../SignalChart', () => ({ __esModule: true, default: () => null }));
jest.mock('../PairSensorDialog', () => ({ __esModule: true, default: () => null }));
jest.mock('../DirectSensorsPanel', () => ({
  __esModule: true,
  useDirectDevices: () => ({
    available: true,
    error: false,
    devices: [
      { id: 'direct-peter', zone_id: 'peter', hardware_id: 'Direct Büro' },
      { id: 'direct-hidden', zone_id: 'hidden', hardware_id: 'Geheim Direct' },
    ],
  }),
  default: ({ feed }: { feed: { devices: { id: string; hardware_id: string }[] } }) => (
    <div>
      {feed.devices.map((device) => (
        <span key={device.id}>{device.hardware_id}</span>
      ))}
    </div>
  ),
}));
jest.mock('@/lib/api', () => ({
  apiListWavFiles: jest.fn(async () => []),
  apiListZones: jest.fn(async () => [
    { id: 'peter', name: 'Peter Büro' },
    { id: 'rudi', name: 'Rudi Meier' },
  ]),
  apiListSensors: jest.fn(async () => [
    {
      id: 'peter-sensor',
      zone_id: 'peter',
      name: 'Büro-Sensor',
      status: 'online',
      mac_address: 'AA',
    },
    {
      id: 'rudi-sensor',
      zone_id: 'rudi',
      name: 'Rudi-Sensor',
      status: 'online',
      mac_address: 'BB',
    },
    {
      id: 'hidden-sensor',
      zone_id: 'hidden',
      name: 'Geheimer Sensor',
      status: 'online',
      mac_address: 'CC',
    },
  ]),
  apiGetSensorData: jest.fn(async () => []),
  apiGetSensorDataAdvanced: jest.fn(async () => []),
  apiCountWavFiles: jest.fn(async () => ({ count: 0, total_size_bytes: 0 })),
}));

beforeEach(() => {
  window.history.replaceState({}, '', '/de/app/sensors');
  sessionStorage.clear();
});

test('groups Gateway and Direct sensors under their permitted zone', async () => {
  render(<SensorsPage />);
  const peter = await screen.findByRole('region', { name: 'Peter Büro' });
  const rudi = screen.getByRole('region', { name: 'Rudi Meier' });
  expect(within(peter).queryByText('Büro-Sensor')).not.toBeInTheDocument();
  fireEvent.click(within(peter).getByRole('button'));
  fireEvent.click(within(rudi).getByRole('button'));
  expect(within(peter).getAllByText('Büro-Sensor').length).toBeGreaterThan(0);
  expect(within(peter).getByText('Direct Büro')).toBeInTheDocument();
  expect(within(rudi).getAllByText('Rudi-Sensor').length).toBeGreaterThan(0);
  expect(within(rudi).queryByText('Direct Büro')).not.toBeInTheDocument();
  expect(screen.queryByText('Geheimer Sensor')).not.toBeInTheDocument();
  expect(screen.queryByText('Geheim Direct')).not.toBeInTheDocument();
  const gateway = within(peter).getByRole('button', { name: /Büro-Sensor/ });
  expect(gateway).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(within(peter).getByRole('button', { name: /Peter Büro/ }));
  expect(within(peter).queryByText('Büro-Sensor')).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('combobox', { name: 'Zone' }), { target: { value: 'rudi' } });
  expect(screen.queryByRole('region', { name: 'Peter Büro' })).not.toBeInTheDocument();
  expect(screen.getByRole('region', { name: 'Rudi Meier' })).toBeInTheDocument();
});

test('restores permitted device and zone but resets old range URLs to 24 hours', async () => {
  window.history.replaceState(
    { router: 'preserved' },
    '',
    '/fr/app/sensors?zone=rudi&sensor=rudi-sensor&range=7d#chart'
  );
  render(<SensorsPage />);
  await waitFor(() =>
    expect(apiGetSensorDataAdvanced).toHaveBeenCalledWith(
      'rudi-sensor',
      expect.objectContaining({
        range: '24h',
        from_dt: expect.any(String),
        to_dt: expect.any(String),
      })
    )
  );
  expect(screen.getByRole('combobox', { name: 'Zone' })).toHaveValue('rudi');
  expect(screen.getByRole('button', { name: '24 Stunden' })).toHaveAttribute(
    'aria-pressed',
    'true'
  );
  expect(screen.queryByRole('button', { name: '7 Tage' })).not.toBeInTheDocument();
  expect(new URLSearchParams(window.location.search).get('sensor')).toBe('rudi-sensor');
  expect(new URLSearchParams(window.location.search).get('range')).toBeNull();
  expect(window.location.hash).toBe('#chart');
});

test('does not restore a sensor outside permitted zones', async () => {
  window.history.replaceState(
    {},
    '',
    '/it/app/sensors?zone=hidden&sensor=hidden-sensor&range=invalid'
  );
  render(<SensorsPage />);
  await screen.findByRole('region', { name: 'Peter Büro' });
  await waitFor(() => expect(new URLSearchParams(window.location.search).get('sensor')).toBeNull());
  expect(new URLSearchParams(window.location.search).get('zone')).toBeNull();
  expect(new URLSearchParams(window.location.search).get('range')).toBeNull();
  expect(screen.queryByText('Geheimer Sensor')).not.toBeInTheDocument();
});

test('keeps a disconnected old sensor visible and its historical measurements accessible', async () => {
  jest.mocked(apiListSensors).mockResolvedValueOnce([
    {
      id: 'retained-sensor',
      zone_id: 'peter',
      gateway_id: 'removed-gateway',
      gateway_name: 'Retained gateway',
      name: 'Retained sensor',
      mac_address: 'DD',
      status: 'disconnected',
      last_seen: '2020-01-01T00:00:00Z',
    },
  ] as Awaited<ReturnType<typeof apiListSensors>>);
  render(<SensorsPage />);
  const zone = await screen.findByRole('region', { name: 'Peter Büro' });
  fireEvent.click(within(zone).getByRole('button', { name: /Peter Büro/ }));
  const retained = within(zone).getByRole('button', { name: /Retained sensor/ });
  expect(retained).toHaveTextContent('Getrennt');
  expect(retained).not.toHaveTextContent('Inaktiv (>3 Tage)');
  fireEvent.click(retained);
  await waitFor(() =>
    expect(apiGetSensorDataAdvanced).toHaveBeenCalledWith(
      'retained-sensor',
      expect.objectContaining({ range: '24h' })
    )
  );
  expect(screen.getByRole('button', { name: '24 Stunden' })).toBeInTheDocument();
});

test('preserves the selected Gateway Live window during a locale switch', async () => {
  window.history.replaceState(
    {},
    '',
    '/de/app/sensors?zone=rudi&sensor=rudi-sensor&range=live&liveWindow=180'
  );
  preserveMeasurementLocale('/fr/app/sensors');
  window.history.replaceState(
    {},
    '',
    '/fr/app/sensors?zone=rudi&sensor=rudi-sensor&range=live&liveWindow=180'
  );
  render(<SensorsPage />);
  expect(await screen.findByRole('button', { name: '3 Minuten' })).toHaveAttribute(
    'aria-pressed',
    'true'
  );
  expect(screen.getByRole('button', { name: 'Live' })).toHaveAttribute('aria-pressed', 'true');
  expect(new URLSearchParams(window.location.search).get('liveWindow')).toBe('180');
});
