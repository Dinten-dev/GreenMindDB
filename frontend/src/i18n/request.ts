import { getRequestConfig } from 'next-intl/server';
import { locales } from './locales';
export { locales } from './locales';

export default getRequestConfig(async ({ requestLocale }) => {
  let locale = await requestLocale;
  if (!locale || !locales.some((supported) => supported === locale)) {
    locale = 'de';
  }
  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
  };
});
