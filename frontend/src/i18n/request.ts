import {getRequestConfig} from 'next-intl/server';
export const locales = ['de', 'en', 'fr'];

export default getRequestConfig(async ({requestLocale}) => {
  let locale = await requestLocale;
  if (!locale || !locales.includes(locale)) {
      locale = 'de';
  }
  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default
  };
});
