import { prepareSignalSeries, resolutionLabel } from './signal-series';

test('preserves verified spikes and leaves unknown signal statistics absent', () => {
  const points = prepareSignalSeries([
    {
      timestamp: '2026-09-01T12:00:00Z',
      value: 10,
      minimum: 1,
      maximum: 100,
      resolution_seconds: 600,
    },
    { timestamp: '2026-09-01T12:10:00Z', value: 11, resolution_seconds: 600 },
  ]);
  expect(points[0].envelope).toEqual([1, 100]);
  expect(points[1].envelope).toBeNull();
});

test('does not draw lines through missing intervals or substitute averages for RMS', () => {
  const data = [
    { timestamp: '2026-09-01T12:00:00Z', value: 10, rms: 20, resolution_seconds: 60 },
    { timestamp: '2026-09-01T12:10:00Z', value: 11, resolution_seconds: 60 },
  ];
  expect(prepareSignalSeries(data).map((p) => p.plotted)).toEqual([10, null, 11]);
  expect(prepareSignalSeries(data, 'rms').map((p) => p.plotted)).toEqual([20, null, null]);
  expect(resolutionLabel(data)).toBe('1 min');
});
