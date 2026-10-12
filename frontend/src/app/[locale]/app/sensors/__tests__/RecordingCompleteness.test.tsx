import { render, screen, waitFor } from '@testing-library/react';
import RecordingCompletenessBadge from '../RecordingCompletenessBadge';
import WavCompletenessList from '../WavCompletenessList';
import { apiListWavFiles, type WavFileInfo } from '@/lib/api';

jest.mock('@/lib/api', () => ({ apiListWavFiles: jest.fn() }));
const files = apiListWavFiles as jest.MockedFunction<typeof apiListWavFiles>;
afterEach(() => jest.resetAllMocks());

test('457-second recordings warn while legacy unknown never appears green', () => {
  const { rerender } = render(
    <RecordingCompletenessBadge
      completeness={{
        status: 'short',
        received_seconds: 457,
        expected_seconds: 600,
        coverage_ratio: 457 / 600,
        basis: 'nominal_bucket',
      }}
    />
  );
  expect(screen.getByText(/Teilstück oder Datenlücke/)).toHaveTextContent('457/600 s');
  expect(screen.getByText(/Teilstück oder Datenlücke/)).toHaveClass('bg-amber-100');
  rerender(<RecordingCompletenessBadge />);
  expect(screen.getByText('Vollständigkeit ungeprüft')).not.toHaveClass('bg-emerald-50');
});

test('intentional start fragments and open Direct recordings remain distinct', () => {
  const { rerender } = render(
    <RecordingCompletenessBadge
      completeness={{ status: 'partial_start', basis: 'verified_manifest' }}
    />
  );
  expect(screen.getByText(/Startteilstück/)).not.toHaveClass('bg-emerald-50');
  rerender(
    <RecordingCompletenessBadge
      completeness={{ status: 'collecting', basis: 'verified_manifest' }}
    />
  );
  expect(screen.getByText(/Aufnahme läuft/)).not.toHaveClass('bg-emerald-50');
});

test('file lookup preserves sensor/date scope and displays uncertainty on errors', async () => {
  files.mockResolvedValueOnce([{ id: 'wav-1', started_at: '2026-10-10T12:00:00Z' } as WavFileInfo]);
  const { rerender } = render(
    <WavCompletenessList sensorId="sensor-a" fromDate="2026-10-09" toDate="2026-10-10" />
  );
  expect(await screen.findByText('Vollständigkeit ungeprüft')).toBeInTheDocument();
  expect(files).toHaveBeenCalledWith('sensor-a', {
    from_dt: '2026-10-09T00:00:00Z',
    to_dt: '2026-10-10T23:59:59Z',
    limit: 100,
  });
  files.mockRejectedValueOnce(new Error('offline'));
  rerender(<WavCompletenessList sensorId="sensor-b" fromDate="2026-10-09" toDate="2026-10-10" />);
  await waitFor(() =>
    expect(screen.getByRole('status')).toHaveTextContent('Vollständigkeit momentan ungeprüft')
  );
});
