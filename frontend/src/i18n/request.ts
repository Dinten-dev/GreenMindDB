import {getRequestConfig} from 'next-intl/server';
import {notFound} from 'next/navigation';

export const locales = ['de', 'en', 'fr'];

export default getRequestConfig(async ({requestLocale}) => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let locale = await requestLocale;
  if (!locale || !locales.includes(locale as any)) {
      locale = 'de';
  }
  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default
  };
});
