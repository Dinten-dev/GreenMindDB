import { createTranslator } from 'next-intl';
import { generateMetadata } from '@/app/[locale]/layout';
import { generateMetadata as observationMetadata } from '@/app/[locale]/observe/layout';
import LegacyObservation from '@/app/observe/[publicId]/page';
import { config } from '@/proxy';
import de from '../../../messages/de.json';
import en from '../../../messages/en.json';
import fr from '../../../messages/fr.json';
import it from '../../../messages/it.json';

jest.unmock('next-intl');
jest.mock('next-intl/middleware', () => ({ __esModule: true, default: jest.fn(() => jest.fn()) }));
jest.mock('next-intl/server', () => ({
  getMessages: jest.fn(),
  getTranslations: async ({
    locale,
    namespace,
  }: {
    locale: 'de' | 'en' | 'fr' | 'it';
    namespace: string;
  }) =>
    jest.requireActual('next-intl').createTranslator({
      locale,
      messages: {
        de: jest.requireActual('../../../messages/de.json'),
        en: jest.requireActual('../../../messages/en.json'),
        fr: jest.requireActual('../../../messages/fr.json'),
        it: jest.requireActual('../../../messages/it.json'),
      }[locale],
      namespace,
    }),
}));
const mockRedirect = jest.fn();
jest.mock('next/navigation', () => ({ redirect: (target: string) => mockRedirect(target) }));
const catalogs = { de, en, fr, it };
test.each(Object.keys(catalogs) as (keyof typeof catalogs)[])(
  'localizes metadata and QR destinations in %s',
  async (locale) => {
    const result = await generateMetadata({ params: Promise.resolve({ locale }) });
    expect(result.title).toBe(catalogs[locale].Metadata.title);
    expect(result.description).toBe(catalogs[locale].Metadata.description);
    const observation = await observationMetadata({ params: Promise.resolve({ locale }) });
    expect(observation.title).toBe(catalogs[locale].ObservationMetadata.title);
    expect(observation.robots).toEqual({ index: false, follow: false });
    await LegacyObservation({
      params: Promise.resolve({ publicId: 'synthetic-id' }),
      searchParams: Promise.resolve({ lang: locale }),
    });
    expect(mockRedirect).toHaveBeenLastCalledWith(`/${locale}/observe/synthetic-id`);
  }
);
test('keeps old QR URLs out of locale middleware and validates locale hints', async () => {
  const matcher = new RegExp('^' + config.matcher[0] + '$');
  expect(matcher.test('/observe/synthetic-id')).toBe(false);
  expect(matcher.test('/it/observe/synthetic-id')).toBe(true);
  await LegacyObservation({
    params: Promise.resolve({ publicId: 'synthetic-id' }),
    searchParams: Promise.resolve({ lang: 'https://invalid.test' }),
  });
  expect(mockRedirect).toHaveBeenLastCalledWith('/de/observe/synthetic-id');
});
test('keeps all metadata and navigation catalogs complete', () => {
  for (const [locale, messages] of Object.entries(catalogs))
    for (const namespace of ['Metadata', 'ObservationMetadata', 'Navigation'] as const) {
      expect(Object.keys(messages[namespace]).sort()).toEqual(Object.keys(de[namespace]).sort());
      const t = createTranslator({
        locale,
        messages,
        namespace,
        onError: (e) => {
          throw e;
        },
      });
      for (const key of Object.keys(messages[namespace])) expect(t(key as never)).not.toBe(key);
    }
});
