import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import GatewaysPage from '../page';
import DashboardPage from '../../dashboard/page';
import {
  ApiError,
  apiListGateways,
  apiListZones,
  apiListSensors,
  apiRemoveGateway,
} from '@/lib/api';
import de from '../../../../../../messages/de.json';
import en from '../../../../../../messages/en.json';
import fr from '../../../../../../messages/fr.json';
import italian from '../../../../../../messages/it.json';
jest.unmock('next-intl');
let mockRole = 'admin';
jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: 'synthetic-user',
      name: 'Test User',
      organization_id: 'org',
      organization_name: 'Synthetic',
      role: mockRole,
    },
    refresh: jest.fn(),
    createOrg: jest.fn(),
  }),
}));
jest.mock('@/lib/api', () => ({
  ...jest.requireActual('@/lib/api'),
  apiListGateways: jest.fn(),
  apiListZones: jest.fn(),
  apiListSensors: jest.fn(),
  apiRemoveGateway: jest.fn(),
}));
const gateway = {
  id: 'gateway',
  zone_id: 'zone',
  name: 'Office gateway fixture',
  hardware_id: 'fixture-gateway',
  status: 'online',
  is_active: true,
  sensor_count: 1,
};
const sensor = {
  id: 'sensor',
  gateway_id: gateway.id,
  zone_id: 'zone',
  name: 'Retained sensor fixture',
  mac_address: '00:00:00:00:00:77',
  status: 'online',
};
let removed = false;
beforeAll(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.setAttribute('open', '');
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.removeAttribute('open');
    },
  });
});
beforeEach(() => {
  jest.clearAllMocks();
  removed = false;
  mockRole = 'admin';
  jest
    .mocked(apiListGateways)
    .mockImplementation(async () =>
      removed ? [] : ([gateway] as Awaited<ReturnType<typeof apiListGateways>>)
    );
  jest
    .mocked(apiListZones)
    .mockImplementation(
      async () =>
        [
          { id: 'zone', name: 'Office fixture', gateway_count: removed ? 0 : 1, sensor_count: 1 },
        ] as Awaited<ReturnType<typeof apiListZones>>
    );
  jest
    .mocked(apiListSensors)
    .mockImplementation(
      async () =>
        [{ ...sensor, status: removed ? 'disconnected' : 'online' }] as Awaited<
          ReturnType<typeof apiListSensors>
        >
    );
  jest.mocked(apiRemoveGateway).mockImplementation(async () => {
    removed = true;
  });
});
const catalogs = { de, en, fr, it: italian };
function view(component: React.ReactNode, locale: keyof typeof catalogs = 'de') {
  return render(
    <NextIntlClientProvider locale={locale} messages={catalogs[locale]} timeZone="UTC">
      {component}
    </NextIntlClientProvider>
  );
}
const pages = [
  { name: 'gateway list', Page: GatewaysPage },
  { name: 'dashboard', Page: DashboardPage },
];
it.each(pages)(
  'removes from $name after confirmation and retains measurements',
  async ({ Page }) => {
    view(<Page />);
    await screen.findByText(gateway.name!);
    fireEvent.click(screen.getByRole('button', { name: 'Gateway entfernen' }));
    const dialog = screen.getByRole('dialog', { name: 'Gateway entfernen?' });
    expect(
      within(dialog).getByText(/Messdaten, Aufnahmen und Zuordnungen bleiben erhalten/)
    ).toBeInTheDocument();
    expect(apiRemoveGateway).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Gateway entfernen' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(apiRemoveGateway).toHaveBeenCalledTimes(1);
    expect(apiRemoveGateway).toHaveBeenCalledWith(gateway.id);
    expect(screen.queryByText(gateway.name!)).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(
      'Gateway entfernt. Messdaten und Zuordnungen bleiben erhalten.'
    );
    if (Page === DashboardPage) {
      expect(screen.getByText(sensor.name)).toBeInTheDocument();
      expect(screen.getByText(/Getrennt/)).toBeInTheDocument();
    }
  }
);
it.each([403, 404])(
  'shows %s errors without claiming removal or changing the list',
  async (status) => {
    jest.mocked(apiRemoveGateway).mockRejectedValueOnce(new ApiError(status, 'raw backend detail'));
    view(<GatewaysPage />);
    await screen.findByText(gateway.name!);
    fireEvent.click(screen.getByRole('button', { name: 'Gateway entfernen' }));
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Gateway entfernen' })
    );
    expect(await screen.findByRole('alert')).not.toHaveTextContent('raw backend detail');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getAllByText(gateway.name!).length).toBeGreaterThan(0);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  }
);
it('distinguishes a successful removal from a failed list refresh; retry only refreshes', async () => {
  view(<GatewaysPage />);
  await screen.findByText(gateway.name!);
  jest.mocked(apiListGateways).mockRejectedValueOnce(new ApiError(503, 'unavailable'));
  fireEvent.click(screen.getByRole('button', { name: 'Gateway entfernen' }));
  fireEvent.click(
    within(screen.getByRole('dialog')).getByRole('button', { name: 'Gateway entfernen' })
  );
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Gateway entfernt. Die Ansicht konnte nicht aktualisiert werden.'
  );
  expect(screen.queryByText(gateway.name!)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Erneut laden' }));
  expect(await screen.findByRole('status')).toHaveTextContent(
    'Messdaten und Zuordnungen bleiben erhalten'
  );
  expect(apiRemoveGateway).toHaveBeenCalledTimes(1);
});
it('keeps confirmation open while pending and prevents duplicate removal', async () => {
  let finish!: () => void;
  jest.mocked(apiRemoveGateway).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      })
  );
  view(<GatewaysPage />);
  await screen.findByText(gateway.name!);
  fireEvent.click(screen.getByRole('button', { name: 'Gateway entfernen' }));
  const dialog = screen.getByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Gateway entfernen' }));
  expect(within(dialog).getByRole('button', { name: 'Entferne…' })).toBeDisabled();
  expect(within(dialog).getByRole('button', { name: 'Abbrechen' })).toBeDisabled();
  fireEvent(dialog, new Event('cancel', { bubbles: true, cancelable: true }));
  expect(dialog).toBeInTheDocument();
  expect(apiRemoveGateway).toHaveBeenCalledTimes(1);
  await act(async () => finish());
});
it('does not resurrect removed devices or online sensor status from stale lists', async () => {
  view(<DashboardPage />);
  await screen.findByText(gateway.name!);
  jest
    .mocked(apiListGateways)
    .mockResolvedValue([gateway] as Awaited<ReturnType<typeof apiListGateways>>);
  jest
    .mocked(apiListSensors)
    .mockResolvedValue([sensor] as Awaited<ReturnType<typeof apiListSensors>>);
  fireEvent.click(screen.getByRole('button', { name: 'Gateway entfernen' }));
  fireEvent.click(
    within(screen.getByRole('dialog')).getByRole('button', { name: 'Gateway entfernen' })
  );
  await screen.findByRole('status');
  expect(screen.queryByText(gateway.name!)).not.toBeInTheDocument();
  expect(screen.getByText(/Getrennt/)).toBeInTheDocument();
});
it('does not offer a working removal action to members', async () => {
  mockRole = 'member';
  view(<GatewaysPage />);
  await screen.findByText(gateway.name!);
  expect(screen.getByRole('button', { name: 'Gateway entfernen' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Gateway entfernen' }));
  expect(apiRemoveGateway).not.toHaveBeenCalled();
});
it.each([
  {
    locale: 'de' as const,
    button: 'Gateway entfernen',
    title: 'Gateway entfernen?',
    phrase: 'Messdaten, Aufnahmen und Zuordnungen bleiben erhalten',
  },
  {
    locale: 'en' as const,
    button: 'Remove gateway',
    title: 'Remove gateway?',
    phrase: 'Measurements, recordings and assignments will be retained',
  },
  {
    locale: 'fr' as const,
    button: 'Retirer la passerelle',
    title: 'Retirer la passerelle ?',
    phrase: 'Les mesures, enregistrements et affectations seront conservés',
  },
  {
    locale: 'it' as const,
    button: 'Rimuovi gateway',
    title: 'Rimuovere il gateway?',
    phrase: 'Misurazioni, registrazioni e assegnazioni saranno conservate',
  },
])('explains non-destructive removal in $locale', async ({ locale, button, title, phrase }) => {
  view(<GatewaysPage />, locale);
  await screen.findByText(gateway.name!);
  fireEvent.click(screen.getByRole('button', { name: button }));
  expect(
    within(screen.getByRole('dialog', { name: title })).getByText(new RegExp(phrase))
  ).toBeInTheDocument();
});
