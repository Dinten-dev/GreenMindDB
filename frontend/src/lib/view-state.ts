type ViewKey = 'sensor' | 'zone' | 'range' | 'direct' | 'directRange';

export function readViewValue(key: ViewKey): string | null {
  if (typeof window === 'undefined') return null;
  const value = new URLSearchParams(window.location.search).get(key);
  return value && value.length <= 128 ? value : null;
}

export function replaceViewValues(values: Partial<Record<ViewKey, string | null>>) {
  const query = new URLSearchParams(window.location.search);
  for (const [key, value] of Object.entries(values)) {
    if (value) query.set(key, value);
    else query.delete(key);
  }
  const suffix = query.toString();
  const target = window.location.pathname + (suffix ? `?${suffix}` : '') + window.location.hash;
  if (target === window.location.pathname + window.location.search + window.location.hash) return;
  window.history.replaceState(window.history.state, '', target);
}
