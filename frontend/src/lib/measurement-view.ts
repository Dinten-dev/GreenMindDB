// A locale switch may restore Live once; opening a measurement otherwise starts at 24 hours.
const HANDOFF = 'greenmind-measurement-locale';
type ModeKey = 'range' | 'directRange';
type Handoff = { path: string; at: number; modes: Partial<Record<ModeKey, string>> };
function handoff(): Handoff | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(HANDOFF) || 'null');
    return value &&
      value.modes &&
      typeof value.modes === 'object' &&
      value.path === window.location.pathname &&
      Date.now() - value.at >= 0 &&
      Date.now() - value.at < 30000
      ? value
      : null;
  } catch {
    return null;
  }
}
export function preserveMeasurementLocale(path: string) {
  try {
    const query = new URLSearchParams(window.location.search);
    sessionStorage.setItem(
      HANDOFF,
      JSON.stringify({
        path,
        at: Date.now(),
        modes: {
          range: query.get('range') === 'live' ? 'live' : '24h',
          directRange: query.get('directRange') === 'live' ? 'live' : '24h',
        },
      })
    );
  } catch {
    /* Storage may be unavailable; the safe default remains 24 hours. */
  }
}
export function initialMeasurementMode(key: ModeKey): '24h' | 'live' {
  return typeof window !== 'undefined' && handoff()?.modes[key] === 'live' ? 'live' : '24h';
}
export function finishMeasurementRestore(key: ModeKey) {
  const value = handoff();
  if (!value) return;
  delete value.modes[key];
  try {
    if (Object.keys(value.modes).length) sessionStorage.setItem(HANDOFF, JSON.stringify(value));
    else sessionStorage.removeItem(HANDOFF);
  } catch {
    /* No persisted view is required. */
  }
}
