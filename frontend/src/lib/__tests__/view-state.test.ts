import { readViewValue, replaceViewValues } from '../view-state';
it('retains view state, the router state and unrelated filters for a locale switch', () => {
  window.history.replaceState(
    { router: 'canary' },
    '',
    '/fr/app/sensors?zone=old&sensor=old&sort=name#signal'
  );
  replaceViewValues({ zone: 'allowed', sensor: 'synthetic', range: '7d' });
  expect(readViewValue('sensor')).toBe('synthetic');
  expect(readViewValue('range')).toBe('7d');
  expect(new URLSearchParams(window.location.search).get('sort')).toBe('name');
  expect(window.location.hash).toBe('#signal');
  expect(window.history.state).toEqual({ router: 'canary' });
  replaceViewValues({ sensor: null, range: null });
  expect(readViewValue('sensor')).toBeNull();
  expect(readViewValue('range')).toBeNull();
  expect(readViewValue('zone')).toBe('allowed');
});
it('ignores oversized device identifiers', () => {
  window.history.replaceState({}, '', '/it/app/sensors?sensor=' + 'x'.repeat(129));
  expect(readViewValue('sensor')).toBeNull();
});
