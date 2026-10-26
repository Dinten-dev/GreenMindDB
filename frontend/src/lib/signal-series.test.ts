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

test.each([300, 3600])(
  'preserves short positive and negative impulses across a %s-second view',
  (duration) => {
    const start = Date.parse('2026-10-10T12:00:00Z');
    const data = Array.from({ length: duration }, (_, second) => ({
      timestamp: new Date(start + second * 1000).toISOString(),
      value: second === 120 ? 100 / 380 : second === 121 ? -80 / 380 : 0,
      minimum: second === 121 ? -80 : 0,
      maximum: second === 120 ? 100 : 0,
      resolution_seconds: 1,
    }));
    const points = prepareSignalSeries(data);
    expect(points).toHaveLength(duration);
    expect(points[120].maximum).toBe(100);
    expect(points[121].minimum).toBe(-80);
    expect(points[120].plotted).toBe(100 / 380);
    expect(prepareSignalSeries(data, 'rms')[120].maximum).toBeNull();
  }
);
