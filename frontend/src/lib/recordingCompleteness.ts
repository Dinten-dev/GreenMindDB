import type { RecordingCompleteness } from './api';

export function nominalCompleteness(
  file: {
    started_at: string;
    duration_seconds: number;
    sample_rate: number;
    completeness?: RecordingCompleteness;
  },
  direct = false
): RecordingCompleteness {
  if (file.completeness) return file.completeness;
  const start = Date.parse(file.started_at) / 1000;
  const rate = file.sample_rate;
  const duration = file.duration_seconds;
  if (
    !Number.isFinite(start) ||
    !Number.isFinite(duration) ||
    !Number.isFinite(rate) ||
    rate <= 0 ||
    duration < 0
  )
    return { status: 'unknown', basis: 'unavailable' };
  const offset = ((start % 600) + 600) % 600;
  // A Direct run alone cannot distinguish intentional startup from a gap.
  const expectedSeconds = direct ? 600 : 600 - offset;
  const received = Math.round(duration * rate);
  const expected = Math.round(expectedSeconds * rate);
  const status =
    received < expected - 1
      ? 'short'
      : received > expected + rate || (direct && offset >= 1)
        ? 'unknown'
        : offset >= 1
          ? 'partial_start'
          : 'complete';
  return {
    status,
    received_samples: received,
    expected_samples: expected,
    received_seconds: duration,
    expected_seconds: expectedSeconds,
    coverage_ratio: Math.min(1, received / expected),
    missing_seconds: Math.max(0, (expected - received) / rate),
    basis: 'nominal_bucket',
    close_reason: 'unknown',
  };
}
