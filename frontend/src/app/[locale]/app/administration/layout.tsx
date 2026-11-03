'use client';

import { useAppText } from '@/i18n/app-text';
import { useEffect, useState, type ReactNode } from 'react';
import { adminCapabilities } from '@/lib/administration-api';
export default function AdministrationLayout({ children }: { children: ReactNode }) {
  const tx = useAppText();

  const [allowed, setAllowed] = useState<boolean | null>(null);
  useEffect(() => {
    let active = true;
    adminCapabilities()
      .then((v) => {
        if (active) setAllowed(v.can_manage);
      })
      .catch(() => {
        if (active) setAllowed(false);
      });
    return () => {
      active = false;
    };
  }, []);
  if (allowed === null) return <p role="status">{tx('Zugriff wird geprüft …')}</p>;
  if (!allowed)
    return (
      <div className="glass-card p-6">
        <h1 className="text-xl font-semibold">{tx('Kein Zugang zur Administration')}</h1>
        <p className="mt-2 text-sm text-gray-500">
          {tx('Diese Funktionen sind nur für freigegebene Administrationskonten verfügbar.')}
        </p>
      </div>
    );
  return children;
}
