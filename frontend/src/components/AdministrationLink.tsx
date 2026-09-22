'use client';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useLocale } from 'next-intl';
import { useAuth } from '@/contexts/AuthContext';
import { adminCapabilities } from '@/lib/administration-api';

export default function AdministrationLink({
  active,
  onNavigate,
}: {
  active: boolean;
  onNavigate?: () => void;
}) {
  const { user } = useAuth();
  const locale = useLocale();
  const userId = user?.id;
  const [allowed, setAllowed] = useState(false);
  useEffect(() => {
    let current = true;
    setAllowed(false);
    if (userId)
      adminCapabilities()
        .then((v) => {
          if (current) setAllowed(v.can_manage);
        })
        .catch(() => {});
    return () => {
      current = false;
    };
  }, [userId]);
  if (!allowed) return null;
  return (
    <Link
      href={`/${locale}/app/administration`}
      onClick={onNavigate}
      aria-current={active ? 'page' : undefined}
      className={`flex items-center gap-3 px-3 py-3 rounded-xl text-sm transition-colors ${active ? 'nav-active' : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'}`}
    >
      <span aria-hidden="true" className="w-5 text-center text-lg leading-none">
        ⚙
      </span>
      <span>Administration</span>
    </Link>
  );
}
