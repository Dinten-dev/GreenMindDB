'use client';

import { useAppText } from '@/i18n/app-text';

import Link from 'next/link';
import { useLocale } from 'next-intl';
import { usePathname, useRouter } from 'next/navigation';
import { useAuth } from '@/contexts/AuthContext';
import { useEffect } from 'react';

const GATEWAY_TABS = [
  { href: '/app/gateway-fleet/overview', label: 'Fleet' },
  { href: '/app/gateway-fleet/releases', label: 'Releases' },
  { href: '/app/gateway-fleet/configs', label: 'Configs' },
  { href: '/app/gateway-fleet/actions', label: 'Actions' },
  { href: '/app/gateway-fleet/logs', label: 'Update Logs' },
  { href: '/app/gateway-fleet/audit', label: 'Audit' },
];

export default function GatewayFleetLayout({ children }: { children: React.ReactNode }) {
  const tx = useAppText();

  const { user, loading } = useAuth();
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && user && user.role !== 'admin') {
      router.replace(`/${locale}/app/dashboard`);
    }
  }, [user, loading, router, locale]);

  if (loading) {
    return (
      <div className="animate-pulse space-y-4">
        <div className="h-8 w-48 bg-black/[0.04] rounded-xl" />
        <div className="h-64 bg-black/[0.04] rounded-2xl" />
      </div>
    );
  }

  if (!user || user.role !== 'admin') {
    return null;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-800 tracking-tight">
          {tx('Gateway Fleet Management')}
        </h1>
        <p className="text-sm text-gray-400 mt-1">
          {tx('Remote Updates, Konfiguration und Steuerung für Raspberry Pi Gateways')}
        </p>
      </div>

      <nav className="flex gap-1 p-1 bg-black/[0.03] rounded-2xl w-fit">
        {GATEWAY_TABS.map((tab) => {
          const href = `/${locale}${tab.href}`;
          const isActive =
            pathname === href ||
            (tab.href !== '/app/gateway-fleet/overview' && pathname.startsWith(href));
          return (
            <Link
              key={tab.href}
              href={href}
              className={`px-4 py-2 rounded-xl text-sm font-medium transition-all duration-200 ${
                isActive ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {tx(tab.label)}
            </Link>
          );
        })}
      </nav>

      {children}
    </div>
  );
}
