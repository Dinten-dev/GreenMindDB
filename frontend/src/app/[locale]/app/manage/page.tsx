'use client';

import Link from 'next/link';
import { useLocale } from 'next-intl';
import { useAuth } from '@/contexts/AuthContext';
import { useAppText } from '@/i18n/app-text';
import AdministrationLink from '@/components/AdministrationLink';

export default function ManagementPage() {
  const tx = useAppText();
  const locale = useLocale();
  const { user } = useAuth();
  const links = [
    { href: '/app/zones', title: 'Zonen', description: 'Standorte und Zugänge verwalten.' },
    { href: '/app/gateways', title: 'Gateways', description: 'Verbindungen und Geräte verwalten.' },
    ...(user?.role === 'admin'
      ? [
          {
            href: '/app/firmware/dashboard',
            title: 'Firmware',
            description: 'Versionen und Aktualisierungen verwalten.',
          },
          {
            href: '/app/gateway-fleet/overview',
            title: 'Gateway-Flotte',
            description: 'Betrieb und Wartung der Gateways.',
          },
        ]
      : []),
  ];
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-gray-800">{tx('Verwaltung')}</h1>
      <div className="grid gap-4 sm:grid-cols-2">
        {links.map((link) => (
          <Link
            key={link.href}
            href={`/${locale}${link.href}`}
            className="glass-card p-6 transition-colors hover:bg-emerald-50 focus-visible:outline-emerald-600"
          >
            <h2 className="font-semibold text-gray-800">{tx(link.title)}</h2>
            <p className="mt-2 text-sm text-gray-600">{tx(link.description)}</p>
          </Link>
        ))}
      </div>
      <AdministrationLink active={false} />
    </div>
  );
}
