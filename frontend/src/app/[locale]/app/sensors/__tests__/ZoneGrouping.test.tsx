import { fireEvent, render, screen, within } from '@testing-library/react';
import SensorsPage from '../page';

jest.mock('next-intl', () => ({ useLocale: () => 'de' }));
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
