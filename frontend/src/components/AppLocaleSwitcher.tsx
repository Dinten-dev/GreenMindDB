'use client';
import { useLocale } from 'next-intl';
import { usePathname, useRouter } from 'next/navigation';
import { useId } from 'react';
import { useAppText } from '@/i18n/app-text';
import { localePath } from '@/lib/auth-routing';
import { preserveMeasurementLocale } from '@/lib/measurement-view';

export default function AppLocaleSwitcher() {
  const locale = useLocale();
  const pathname = usePathname();
  const router = useRouter();
  const tx = useAppText();
  const id = useId();
  return (
    <div className="flex items-center justify-between gap-3 text-sm">
      <label htmlFor={id}>{tx('Sprache')}</label>
      <select
        id={id}
        value={locale}
        className="max-w-36 rounded-lg border border-gray-200 bg-white px-2 py-2"
        onChange={(event) => {
          const target = localePath(event.target.value, pathname);
          preserveMeasurementLocale(target);
          router.push(target + window.location.search + window.location.hash);
        }}
      >
        <option value="de">Deutsch</option>
        <option value="en">English</option>
        <option value="fr">Français</option>
        <option value="it">Italiano</option>
      </select>
    </div>
  );
}
