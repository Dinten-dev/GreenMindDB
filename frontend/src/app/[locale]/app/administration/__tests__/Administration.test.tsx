import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import StorageMonitor from '../StorageMonitor';
import UserManagement from '../users/page';
import AdministrationLink from '@/components/AdministrationLink';
import {
  adminStorage,
  adminCapabilities,
  adminCatalog,
  adminUsers,
  adminCreateUser,
  adminUpdateUser,
} from '@/lib/administration-api';
jest.mock('next-intl', () => ({ useLocale: () => 'de' }));
jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 'account' } }) }));
jest.mock('@/lib/administration-api');
const storage = jest.mocked(adminStorage);
afterEach(() => jest.resetAllMocks());
test.each([4.99, 5, 5.01])('warns only below five percent (%s)', async (free) => {
  storage.mockResolvedValue({
    total_bytes: 1000,
    available_bytes: free * 10,
    used_bytes: 1000 - free * 10,
    free_percent: free,
    used_percent: 100 - free,
    warning: free < 5,
    warning_threshold_percent: 5,
    checked_at: '2026-09-22T10:00:00Z',
  });
  render(<StorageMonitor />);
  expect(await screen.findByRole('meter')).toHaveAttribute('aria-valuenow', String(100 - free));
  expect(screen.queryByRole('alert') !== null).toBe(free < 5);
});
test('unavailable storage is not presented as a full disk', async () => {
  storage.mockRejectedValue(new Error('Offline'));
  render(<StorageMonitor />);
  expect(await screen.findByText(/Speicherstatus momentan nicht verfügbar/)).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.queryByRole('meter')).not.toBeInTheDocument();
});
test('ordinary users see no additional management link', async () => {
  jest.mocked(adminCapabilities).mockResolvedValue({ can_manage: false });
  render(<AdministrationLink active={false} />);
  await waitFor(() => expect(adminCapabilities).toHaveBeenCalled());
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
});
test('allowlisted admins get the management link', async () => {
  jest.mocked(adminCapabilities).mockResolvedValue({ can_manage: true });
  render(<AdministrationLink active />);
  expect(await screen.findByRole('link', { name: 'Administration' })).toHaveAttribute(
    'href',
    '/de/app/administration'
  );
});
test('company changes clear previous zone grants before saving', async () => {
  jest.mocked(adminCatalog).mockResolvedValue({
    companies: [
      { id: 'a', name: 'Company A' },
      { id: 'b', name: 'Company B' },
    ],
    zones: [
      { id: 'za', name: 'Zone A', organization_id: 'a' },
      { id: 'zb', name: 'Zone B', organization_id: 'b' },
    ],
  });
  const user = {
    id: 'u',
    name: 'Member',
    email: 'member@example.com',
    phone_number: null,
    organization_id: 'a',
    organization_name: 'Company A',
    role: 'member' as const,
    is_active: true,
    is_verified: true,
    zone_ids: ['za'],
    visible_zones: [{ id: 'za', name: 'Zone A' }],
    access_note: 'Nur freigegebene Zonen.',
    all_zones: false,
    protected: false,
  };
  jest.mocked(adminUsers).mockResolvedValue({ users: [user], total: 1 });
  jest
    .mocked(adminUpdateUser)
    .mockResolvedValue({ ...user, organization_id: 'b', zone_ids: ['zb'] });
  render(<UserManagement />);
  fireEvent.click(await screen.findByRole('button', { name: 'Bearbeiten' }));
  expect(screen.getByLabelText('Zone A')).toBeChecked();
  fireEvent.change(screen.getByLabelText('Firma'), { target: { value: 'b' } });
  expect(screen.queryByLabelText('Zone A')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Zone B')).not.toBeChecked();
  fireEvent.click(screen.getByLabelText('Zone B'));
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  await waitFor(() =>
    expect(adminUpdateUser).toHaveBeenCalledWith(
      'u',
      expect.objectContaining({ organization_id: 'b', zone_ids: ['zb'] })
    )
  );
  expect(adminCreateUser).not.toHaveBeenCalled();
});

test('shows effective zones and requires email confirmation before deletion', async () => {
  const { adminDeleteUser } = await import('@/lib/administration-api');
  jest.mocked(adminCatalog).mockResolvedValue({ companies: [], zones: [] });
  const customer = {
    id: 'customer',
    name: 'Customer',
    email: 'customer@example.com',
    phone_number: null,
    organization_id: 'company',
    organization_name: 'Company',
    role: 'member' as const,
    is_active: true,
    is_verified: true,
    zone_ids: ['a'],
    visible_zones: [{ id: 'a', name: 'Peter Büro' }],
    access_note: 'Nur ausdrücklich freigegebene Zonen dieser Firma.',
    all_zones: false,
    protected: false,
  };
  jest.mocked(adminUsers).mockResolvedValue({ users: [customer], total: 1 });
  jest.mocked(adminDeleteUser).mockResolvedValue(undefined);
  render(<UserManagement />);
  expect(await screen.findByText('Peter Büro')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Konto löschen' }));
  expect(screen.getByRole('button', { name: 'Endgültig löschen' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('E-Mail zur Bestätigung'), {
    target: { value: 'wrong@example.com' },
  });
  expect(screen.getByRole('button', { name: 'Endgültig löschen' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('E-Mail zur Bestätigung'), {
    target: { value: customer.email },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Endgültig löschen' }));
  await waitFor(() => expect(adminDeleteUser).toHaveBeenCalledWith('customer', customer.email));
  expect(await screen.findByText(/Benutzerkonto gelöscht/)).toBeInTheDocument();
});

test('company editing changes the name without editing zone assignments', async () => {
  const { default: Companies } = await import('../companies/page');
  const { adminUpdateCompany } = await import('@/lib/administration-api');
  jest.mocked(adminCatalog).mockResolvedValue({
    companies: [{ id: 'a', name: 'Original' }],
    zones: [{ id: 'z', name: 'Greenhouse', organization_id: 'a' }],
  });
  jest.mocked(adminUpdateCompany).mockResolvedValue({ id: 'a', name: 'Renamed' });
  render(<Companies />);
  fireEvent.click(await screen.findByRole('button', { name: 'Firma bearbeiten' }));
  fireEvent.change(screen.getByLabelText('Firmenname'), { target: { value: 'Renamed' } });
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  await waitFor(() => expect(adminUpdateCompany).toHaveBeenCalledWith('a', 'Renamed'));
  expect(await screen.findByText(/Zonenzugänge bleiben unverändert/)).toBeInTheDocument();
});
