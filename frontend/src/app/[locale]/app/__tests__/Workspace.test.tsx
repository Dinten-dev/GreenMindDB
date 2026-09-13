import { fireEvent, render, screen } from '@testing-library/react';
import AppLayout from '../layout';
import DashboardPage from '../dashboard/page';
import { apiListZones, apiListGateways, apiListSensors } from '@/lib/api';

let mockRole = 'member';
const mockPush = jest.fn();
jest.mock('next/navigation', () => ({
  usePathname: () => '/de/app/plants/plant-id',
  useRouter: () => ({ push: mockPush }),
}));
jest.mock('@/contexts/AuthContext', () => ({
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
  useAuth: () => ({ user: { role: mockRole, organization_id: 'test-org' }, loading: false }),
}));
jest.mock('../sensors/PairSensorDialog', () => ({ __esModule: true, default: () => null }));
jest.mock('@/lib/api', () => ({
  apiListZones: jest.fn(),
  apiListGateways: jest.fn(),
  apiListSensors: jest.fn(),
  gatewayHasWavIssue: () => false,
}));

beforeEach(() => {
  mockRole = 'member';
  jest.clearAllMocks();
});

it('keeps the plant section active on a translated detail route and hides admin links', () => {
  render(
    <AppLayout>
      <p>Inhalt</p>
    </AppLayout>
  );
  expect(screen.getByRole('link', { name: 'Pflanzen' })).toHaveAttribute('aria-current', 'page');
  expect(screen.getByRole('link', { name: 'Messungen & Sensoren' })).toHaveAttribute(
    'href',
    '/de/app/sensors'
  );
  expect(screen.queryByRole('link', { name: 'Firmware' })).not.toBeInTheDocument();
});

it('keeps firmware and fleet navigation available to admins', () => {
  mockRole = 'admin';
  render(
    <AppLayout>
      <p>Inhalt</p>
    </AppLayout>
  );
  expect(screen.getByRole('link', { name: 'Firmware' })).toHaveAttribute(
    'href',
    '/de/app/firmware/dashboard'
  );
  expect(screen.getByRole('link', { name: 'Gateway-Flotte' })).toBeInTheDocument();
});

it('shows a connection failure instead of empty inventory and recovers on retry', async () => {
  const log = jest.spyOn(console, 'error').mockImplementation(() => {});
  jest.mocked(apiListZones).mockRejectedValueOnce(new Error('offline')).mockResolvedValue([]);
  jest.mocked(apiListGateways).mockResolvedValue([]);
  jest.mocked(apiListSensors).mockResolvedValue([]);
  render(<DashboardPage />);
  expect(await screen.findByRole('alert')).toHaveTextContent('konnten nicht geladen');
  expect(screen.queryByText('Noch keine Gateways')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Erneut laden' }));
  expect(await screen.findByRole('link', { name: 'Erste Zone erstellen' })).toHaveAttribute(
    'href',
    '/de/app/zones'
  );
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  log.mockRestore();
});
