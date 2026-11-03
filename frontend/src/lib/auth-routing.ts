const locales = ['de', 'en', 'fr'];

export function localePath(locale: string, path: string) {
  const prefix = locales.includes(locale) ? locale : 'de';
  const stripped = path.replace(/^\/(de|en|fr)(?=\/|$)/, '');
  return `/${prefix}${stripped.startsWith('/') ? stripped : `/${stripped}`}`;
}

export function safeReturnTo(locale: string, target: string | null | undefined) {
  const fallback = localePath(locale, '/app/dashboard');
  if (!target || !target.startsWith('/') || target.startsWith('//') || target.includes('\\')) {
    return fallback;
  }
  try {
    const url = new URL(target, 'https://greenmind.invalid');
    const prefix = localePath(locale, '/app');
    if (
      url.origin !== 'https://greenmind.invalid' ||
      !(url.pathname === prefix || url.pathname.startsWith(`${prefix}/`))
    ) {
      return fallback;
    }
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return fallback;
  }
}

export function signInPath(locale: string, target: string, expired: boolean) {
  const query = new URLSearchParams({ next: safeReturnTo(locale, target) });
  if (expired) query.set('reason', 'expired');
  return `${localePath(locale, '/login')}?${query}`;
}
