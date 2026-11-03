import createMiddleware from 'next-intl/middleware';
import { locales } from './i18n/locales';

export default createMiddleware({
  locales,
  defaultLocale: 'de',
  localePrefix: 'always',
});

export const config = {
  matcher: ['/((?!api|_next|_vercel|.*\\..*).*)'],
};
