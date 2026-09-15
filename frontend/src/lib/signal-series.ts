import type { DataPoint } from './api';

export function prepareSignalSeries(points: DataPoint[], metric: 'value' | 'rms' = 'value') {
  const result: {
    timestamp: string;
    time: number;
    plotted: number | null;
    envelope: [number, number] | null;
    coverage_ratio?: number | null;
    resolution_seconds?: number;
  }[] = [];
  points.forEach((point, index) => {
    const previous = points[index - 1];
    const step = Math.max(
      point.resolution_seconds ?? previous?.resolution_seconds ?? 60,
      previous?.resolution_seconds ?? point.resolution_seconds ?? 60
    );
    if (previous && Date.parse(point.timestamp) - Date.parse(previous.timestamp) > step * 1500) {
      result.push({
        timestamp: new Date(Date.parse(previous.timestamp) + step * 1000).toISOString(),
        time: Date.parse(previous.timestamp) + step * 1000,
        plotted: null,
        envelope: null,
      });
    }
    result.push({
      ...point,
      time: Date.parse(point.timestamp),
      plotted: metric === 'value' ? point.value : (point.rms ?? null),
      envelope:
        metric === 'value' && point.minimum != null && point.maximum != null
          ? [point.minimum, point.maximum]
          : null,
    });
  });
  return result;
}

export function resolutionLabel(points: DataPoint[]) {
  const steps = [...new Set(points.map((point) => point.resolution_seconds).filter(Boolean))];
  if (!steps.length) return 'Bestehende Messwerte';
  return steps
    .sort((a, b) => a! - b!)
    .map((step) => (step! < 60 ? `${step} s` : `${Number((step! / 60).toFixed(2))} min`))
    .join(' / ');
}
