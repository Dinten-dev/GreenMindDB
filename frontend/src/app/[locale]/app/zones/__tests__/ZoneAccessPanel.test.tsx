import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ZoneAccessPanel from '../ZoneAccessPanel';
import { apiUpdateMemberZones, type Zone } from '@/lib/api';

jest.mock('@/lib/api', () => ({
  apiListMemberZones: jest.fn(async () => [
    {
      id: 'member',
      name: 'Peter',
      email: 'peter@example.com',
      role: 'member',
      all_zones: false,
      zone_ids: ['peter'],
    },
    {
      id: 'owner',
      name: 'Eigentümer',
      email: 'owner@example.com',
      role: 'owner',
      all_zones: true,
      zone_ids: [],
    },
  ]),
  apiUpdateMemberZones: jest.fn(async (id, zoneIds) => ({
    id,
    name: 'Peter',
    all_zones: false,
    zone_ids: zoneIds,
  })),
}));
const zones = [
  { id: 'peter', name: 'Peter Büro' },
  { id: 'rudi', name: 'Rudi Meier' },
] as Zone[];

test('saves multiple zone grants without granting all zones', async () => {
  render(<ZoneAccessPanel zones={zones} />);
  fireEvent.change(await screen.findByRole('combobox', { name: 'Person' }), {
    target: { value: 'member' },
  });
  expect(screen.getByRole('checkbox', { name: 'Peter Büro' })).toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Rudi Meier' }));
  fireEvent.click(screen.getByRole('button', { name: 'Zugänge speichern' }));
  await waitFor(() =>
    expect(apiUpdateMemberZones).toHaveBeenCalledWith('member', ['peter', 'rudi'])
  );
  expect(await screen.findByRole('status')).toHaveTextContent('Zonenzugänge gespeichert.');
});

test('owner access is explained and cannot be removed with zone checkboxes', async () => {
  render(<ZoneAccessPanel zones={zones} />);
  fireEvent.change(await screen.findByRole('combobox', { name: 'Person' }), {
    target: { value: 'owner' },
  });
  expect(screen.getByText(/durch ihre Rolle Zugriff auf alle Zonen/)).toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Zugänge speichern' })).not.toBeInTheDocument();
});
