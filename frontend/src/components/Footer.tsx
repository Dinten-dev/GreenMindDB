'use client';

import Link from 'next/link';
import { useTranslations, useLocale } from 'next-intl';
import { usePathname } from 'next/navigation';

export default function Footer() {
    const currentYear = new Date().getFullYear();
    const t = useTranslations('Navigation');
    const locale = useLocale();
    const pathname = usePathname();
    
    if (pathname.startsWith(`/${locale}/app`)) return null;
    
    return (
        <footer className="w-full border-t border-apple-gray-200/50 bg-white/50 backdrop-blur-md py-8 mt-auto">
            <div className="max-w-[1280px] mx-auto px-6 flex flex-col md:flex-row justify-between items-center gap-4">
                <div className="text-sm text-apple-gray-500">
                    &copy; {currentYear} Galaxyadvisors AG. {t('rights')}
                </div>
                <div className="flex gap-6 text-sm">
                    <Link href={`/${locale}/contact`} className="text-apple-gray-500 hover:text-gm-green-600 transition-colors">
                        {t('contact')}
                    </Link>
                    <Link href={`/${locale}/impressum`} className="text-apple-gray-500 hover:text-gm-green-600 transition-colors">
                        {t('impressum')}
                    </Link>
                </div>
            </div>
        </footer>
    );
}
