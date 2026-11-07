'use client';

import Link from 'next/link';
import { useTranslations, useLocale } from 'next-intl';
import { usePathname } from 'next/navigation';

export default function Footer() {
  const currentYear = new Date().getFullYear();
  const t = useTranslations('Navigation');
  const locale = useLocale();
  const pathname = usePathname();

  if (pathname?.startsWith(`/${locale}/app`) || pathname?.startsWith(`/${locale}/observe/`))
    return null;

  return (
    <footer className="w-full border-t border-mercury-line bg-mercury-bg mt-auto">
      <div className="max-w-[1200px] mx-auto px-6 pt-9 pb-12 flex flex-col md:flex-row flex-wrap justify-between gap-5 text-[13px] text-mercury-muted">
        <div>
          &copy; {currentYear} GreenMind GmbH. {t('rights')}
        </div>
        <div className="flex flex-wrap gap-6">
          <a
            href="mailto:info@green-mind.ch"
            className="text-mercury-muted hover:text-mercury-accent transition-colors"
          >
            info@green-mind.ch
          </a>
          <Link
            href={`/${locale}/about`}
            className="text-mercury-muted hover:text-mercury-accent transition-colors"
          >
            {t('about')}
          </Link>
          <Link
            href={`/${locale}/contact`}
            className="text-mercury-muted hover:text-mercury-accent transition-colors"
          >
            {t('contact')}
          </Link>
          <Link
            href={`/${locale}/impressum`}
            className="text-mercury-muted hover:text-mercury-accent transition-colors"
          >
            {t('impressum')}
          </Link>
        </div>
      </div>
    </footer>
  );
}
