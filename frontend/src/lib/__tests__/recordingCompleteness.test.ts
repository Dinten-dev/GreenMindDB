import { nominalCompleteness } from '../recordingCompleteness';

const file = { started_at: '2026-10-10T12:00:00Z', sample_rate: 380, duration_seconds: 457 };
test('legacy 457-second gateway file exposes its 143-second deficit', () => {
  expect(nominalCompleteness(file)).toMatchObject({
    status: 'short',
    received_samples: 173660,
    expected_samples: 228000,
    missing_seconds: 143,
  });
});
test('gateway starts use the remaining window, without certifying short files', () => {
  const partial = { ...file, started_at: '2026-10-10T12:05:00Z', duration_seconds: 300 };
  expect(nominalCompleteness(partial).status).toBe('partial_start');
  expect(nominalCompleteness({ ...partial, duration_seconds: 250 }).status).toBe('short');
});
test('direct run metadata cannot certify an intentional partial start', () => {
  expect(nominalCompleteness({ ...file, duration_seconds: 600 }, true).status).toBe('complete');
  expect(
    nominalCompleteness(
      { ...file, started_at: '2026-10-10T12:05:00Z', duration_seconds: 300 },
      true
    ).status
  ).toBe('short');
  expect(nominalCompleteness({ ...file, sample_rate: 0 }).status).toBe('unknown');
});
test('verified server metadata takes precedence over nominal inference', () => {
  const completeness = { status: 'collecting' as const, basis: 'verified_manifest' };
  expect(nominalCompleteness({ ...file, completeness }, true)).toBe(completeness);
});
