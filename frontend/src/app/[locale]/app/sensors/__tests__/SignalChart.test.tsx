import { render, screen } from '@testing-library/react';
import SignalChart from '../SignalChart';

jest.mock('recharts', () => {
  const actual = jest.requireActual('recharts');
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactElement }) => {
      const { cloneElement } = jest.requireActual('react');
      return cloneElement(children, { width: 800, height: 240 });
    },
  };
});

test('draws both impulse extrema as visible lines, preserving the mean separately', () => {
  const data = [0, 1, 2, 3, 4].map((second) => ({
    timestamp: new Date(Date.parse('2026-10-10T12:00:00Z') + second * 1000).toISOString(),
    value: second === 2 ? 100 / 380 : 0,
    minimum: second === 3 ? -80 : 0,
    maximum: second === 2 ? 100 : 0,
    resolution_seconds: 1,
  }));
  const { container, rerender } = render(
    <SignalChart
      series={{ sensor_id: 'direct-a', kind: 'bio_signal_ch1', unit: 'mV', source: 'direct', data }}
      color="#10b981"
      unit="mV"
      formatTick={(timestamp) => timestamp}
      showPeaks
    />
  );
  const lines = container.querySelectorAll('.recharts-line-curve');
  expect(lines).toHaveLength(3);
  expect(lines[1].getAttribute('d')).not.toEqual(lines[0].getAttribute('d'));
  expect(lines[2].getAttribute('d')).not.toEqual(lines[0].getAttribute('d'));
  expect(lines[1]).toHaveAttribute('stroke-width', '1.5');
  expect(lines[2]).toHaveAttribute('stroke-width', '1.5');
  expect(screen.getByText(/Minimum und Maximum jedes Zeitfensters/)).toBeInTheDocument();
  rerender(
    <SignalChart
      series={{ sensor_id: 'direct-a', kind: 'bio_signal_ch1', unit: 'mV', data }}
      color="#10b981"
      unit="mV"
      formatTick={(timestamp) => timestamp}
    />
  );
  expect(container.querySelectorAll('.recharts-line-curve')).toHaveLength(1);
});
