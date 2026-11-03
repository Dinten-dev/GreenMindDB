import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import PlantDetailPage from '../[plantId]/page';
import PrintQRPage from '../[plantId]/print-qr/page';
import { apiCreateObservationAccess, apiGetObservationAccess, apiGetPlant } from '@/lib/plants-api';

jest.mock('next/navigation', () => ({
  useParams: () => ({ plantId: 'synthetic-plant' }),
  useRouter: () => ({ push: jest.fn() }),
}));
jest.mock('next/link', () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));
jest.mock('react-qr-code', () => ({
  __esModule: true,
  default: () => <svg data-testid="qr-code" />,
}));
jest.mock('@/lib/plants-api', () => ({
  apiGetPlant: jest.fn(),
  apiGetSensorHistory: jest.fn().mockResolvedValue([]),
  apiGetObservationAccess: jest.fn(),
  apiCreateObservationAccess: jest.fn(),
  apiRevokeObservationAccess: jest.fn(),
  apiAssignSensor: jest.fn(),
  apiDeletePlant: jest.fn(),
}));
jest.mock('@/lib/api', () => ({ apiListSensors: jest.fn().mockResolvedValue([]) }));

const active = {
  id: 'synthetic-access',
  plant_id: 'synthetic-plant',
  public_id: 'synthetic-public-id',
  is_active: true,
  usage_count: 0,
  revoked_at: null,
};

beforeEach(() => {
  jest.clearAllMocks();
  (apiGetPlant as jest.Mock).mockResolvedValue({ id: 'synthetic-plant', name: 'Testpflanze' });
  (apiGetObservationAccess as jest.Mock).mockResolvedValue(null);
  (apiCreateObservationAccess as jest.Mock).mockResolvedValue(active);
  jest.spyOn(window, 'confirm').mockReturnValue(true);
});

afterEach(() => jest.restoreAllMocks());

it('does not issue an access on detail-page load', async () => {
  render(<PlantDetailPage />);
  await screen.findByText('Kein aktiver Zugang generiert.');
  expect(apiGetObservationAccess).toHaveBeenCalledWith('synthetic-plant');
  expect(apiCreateObservationAccess).not.toHaveBeenCalled();
});

it('reissues only after the explicit confirmed action', async () => {
  (apiGetObservationAccess as jest.Mock).mockResolvedValue({ ...active, is_active: false });
  render(<PlantDetailPage />);
  fireEvent.click(await screen.findByText('Zugang generieren'));
  await waitFor(() =>
    expect(apiCreateObservationAccess).toHaveBeenCalledWith('synthetic-plant', true)
  );
  await screen.findByText('synthetic-public-id');
});

it('does not reissue when confirmation is cancelled', async () => {
  jest.spyOn(window, 'confirm').mockReturnValue(false);
  render(<PlantDetailPage />);
  fireEvent.click(await screen.findByText('Zugang generieren'));
  expect(apiCreateObservationAccess).not.toHaveBeenCalled();
});

it('cannot issue or print an inactive access on the print page', async () => {
  (apiGetObservationAccess as jest.Mock).mockResolvedValue({ ...active, is_active: false });
  render(<PrintQRPage />);
  await screen.findByText(/Kein aktiver öffentlicher Zugang/);
  expect(screen.queryByTestId('qr-code')).not.toBeInTheDocument();
  expect(apiCreateObservationAccess).not.toHaveBeenCalled();
});

it('prints an existing active access without issuing another', async () => {
  (apiGetObservationAccess as jest.Mock).mockResolvedValue(active);
  render(<PrintQRPage />);
  await screen.findByTestId('qr-code');
  expect(apiCreateObservationAccess).not.toHaveBeenCalled();
});

it('keeps revocation available when access state cannot be read', async () => {
  (apiGetObservationAccess as jest.Mock).mockRejectedValue(new Error('Conflict'));
  render(<PlantDetailPage />);
  await screen.findByText(/Zugangsstatus konnte nicht geladen werden/);
  expect(screen.queryByText('Kein aktiver Zugang generiert.')).not.toBeInTheDocument();
  expect(screen.getByText('Öffentliche Zugänge widerrufen')).toBeInTheDocument();
  expect(apiCreateObservationAccess).not.toHaveBeenCalled();
});

it('distinguishes print-page access errors from inactive access', async () => {
  (apiGetObservationAccess as jest.Mock).mockRejectedValue(new Error('Unavailable'));
  render(<PrintQRPage />);
  await screen.findByText(/Zugangsstatus konnte nicht geladen werden/);
  expect(screen.queryByText(/Kein aktiver öffentlicher Zugang/)).not.toBeInTheDocument();
  expect(screen.queryByTestId('qr-code')).not.toBeInTheDocument();
});
