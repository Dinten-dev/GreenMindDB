import { localePath, safeReturnTo, signInPath } from '../auth-routing';
import de from '../../../messages/de.json';
import en from '../../../messages/en.json';
import fr from '../../../messages/fr.json';

it.each([
  'https://example.invalid/en/app',
  '//example.invalid',
  '/\\example.invalid',
  '/en/app/../../login',
  '/en/application',
  '/de/app/dashboard',
  '/en/login',
  'javascript:alert(1)',
  null,
])('rejects an unsafe or wrong-locale return destination: %s', (target) => {
  expect(safeReturnTo('en', target)).toBe('/en/app/dashboard');
});
it('retains a local app path with query and fragment', () => {
  expect(safeReturnTo('fr', '/fr/app/sensors?view=24h#chart')).toBe(
    '/fr/app/sensors?view=24h#chart'
  );
  expect(signInPath('fr', '/fr/app/sensors?view=24h', true)).toBe(
    '/fr/login?next=%2Ffr%2Fapp%2Fsensors%3Fview%3D24h&reason=expired'
  );
  expect(localePath('fr', '/en/login')).toBe('/fr/login');
});
it('has complete sign-in messages in all existing languages', () => {
  for (const messages of [en, fr]) {
    expect(Object.keys(messages.Auth).sort()).toEqual(Object.keys(de.Auth).sort());
    expect(Object.keys(messages.Auth.errors).sort()).toEqual(Object.keys(de.Auth.errors).sort());
    expect(Object.values(messages.Auth.errors).every((value) => value.length > 10)).toBe(true);
  }
});
