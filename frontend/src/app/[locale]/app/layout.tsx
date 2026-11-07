'use client';

import { useAppText } from '@/i18n/app-text';

import BrandLogo from '@/components/BrandLogo';
import AppLocaleSwitcher from '@/components/AppLocaleSwitcher';

import { useEffect, useState, useRef } from 'react';
import Link from 'next/link';
import { useLocale, useTranslations } from 'next-intl';
import { useRouter, usePathname } from 'next/navigation';
import { useAuth } from '@/contexts/AuthContext';
import { signInPath } from '@/lib/auth-routing';

const ICONS = {
  dashboard: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="2" />
      <rect x="14" y="3" width="7" height="7" rx="2" />
      <rect x="3" y="14" width="7" height="7" rx="2" />
      <rect x="14" y="14" width="7" height="7" rx="2" />
    </>
  ),
  plants: (
    <>
      <path d="M12 21v-9" />
      <path d="M12 12c0-4 3-7 8-7 0 5-3 7-8 7z" />
      <path d="M12 14c0-3-2.5-5-7-5 0 4 2.5 5 7 5z" />
    </>
  ),
  sensors: <path d="M2 12h3l3-7 4 14 4-14 3 7h3" />,
  manage: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1" />
    </>
  ),
};

function NavIcon({ name }: { name: keyof typeof ICONS }) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="shrink-0"
    >
      {ICONS[name]}
    </svg>
  );
}

const NAV_LINKS = [
  { href: '/app/dashboard', label: 'Überblick', icon: 'dashboard' },
  { href: '/app/plants', label: 'Pflanzen', icon: 'plants' },
  { href: '/app/sensors', label: 'Messungen', icon: 'sensors' },
  { href: '/app/manage', label: 'Verwaltung', icon: 'manage' },
] as const;
const managementPaths = [
  '/app/manage',
  '/app/gateways',
  '/app/zones',
  '/app/firmware',
  '/app/gateway-fleet',
  '/app/administration',
];

function SidebarContent({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  const tx = useAppText();

  const { user, logout } = useAuth();
  const locale = useLocale();
  const localPath = pathname.replace(new RegExp(`^/${locale}(?=/|$)`), '');

  const handleLogout = async () => {
    try {
      await logout();
    } catch {
      // Shared state presents the connection error and retry action.
    }
  };

  return (
    <>
      {/* Logo */}
      <div className="px-5 h-20 flex items-center">
        <Link
          href={`/${locale}/app/dashboard`}
          className="flex items-center gap-2.5"
          onClick={onNavigate}
        >
          <BrandLogo size={32} />
          <span className="font-semibold text-[18px] text-gray-800 tracking-[-0.02em]">
            {tx('GreenMind')}
          </span>
        </Link>
      </div>

      <nav
        aria-label={tx('Hauptnavigation')}
        className="flex-1 overflow-y-auto px-3.5 py-3 space-y-0.5"
      >
        {NAV_LINKS.map((link) => {
          const isActive =
            link.href === '/app/manage'
              ? managementPaths.some(
                  (path) => localPath === path || localPath.startsWith(path + '/')
                )
              : localPath === link.href || localPath.startsWith(link.href + '/');
          return (
            <Link
              key={link.href}
              href={`/${locale}${link.href}`}
              onClick={onNavigate}
              aria-current={isActive ? 'page' : undefined}
              className={`flex items-center gap-3 px-3 py-[11px] rounded-xl text-sm transition-colors ${isActive ? 'nav-active' : 'text-gray-700 hover:bg-gray-50 hover:text-gray-900'}`}
            >
              <NavIcon name={link.icon} />
              <span>{tx(link.label)}</span>
            </Link>
          );
        })}
      </nav>

      <div className="px-6 py-3">
        <AppLocaleSwitcher />
      </div>
      <Link
        href={`/${locale}/app/account`}
        onClick={onNavigate}
        className="mx-6 mb-3 text-sm text-gray-600 hover:text-emerald-700"
      >
        {tx('Mein Konto')}
      </Link>
      {/* User */}
      <div className="px-3 py-4 border-t border-gray-200">
        <div className="px-3 py-2">
          <p className="text-sm font-medium text-gray-800 truncate">{user?.name || user?.email}</p>
          <p className="text-xs text-gray-400 truncate">{user?.email}</p>
        </div>
        <button
          onClick={handleLogout}
          className="w-full mt-1 px-3 py-2 text-sm text-gray-500 hover:text-gray-800 hover:bg-gray-50 rounded-xl transition-colors duration-200 text-left"
        >
          {tx('Abmelden')}
        </button>
      </div>
    </>
  );
}

function AppSidebar() {
  const pathname = usePathname();

  return (
    <aside className="glass-sidebar w-64 h-dvh flex-col fixed left-0 top-0 z-30 hidden md:flex">
      <SidebarContent pathname={pathname} />
    </aside>
  );
}

function MobileDrawer() {
  const tx = useAppText();

  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    setOpen(false);
  }, [pathname]);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (open && !dialog?.open) dialog?.showModal();
    if (!open && dialog?.open) dialog.close();
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const desktop = window.matchMedia('(min-width: 768px)');
    const closeOnDesktop = () => {
      if (desktop.matches) setOpen(false);
    };
    desktop.addEventListener('change', closeOnDesktop);
    return () => {
      document.body.style.overflow = previousOverflow;
      desktop.removeEventListener('change', closeOnDesktop);
    };
  }, [open]);

  return (
    <>
      <div className="fixed inset-x-0 top-0 z-30 h-16 bg-white border-b border-gray-100 flex items-center px-4 md:hidden">
        <button
          onClick={() => setOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls="app-mobile-navigation"
          className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-gray-700 hover:bg-gray-50"
        >
          <span aria-hidden="true">☰</span> {tx('Menü')}
        </button>
        <span className="ml-auto text-sm font-semibold text-gray-800">{tx('GreenMind')}</span>
      </div>
      <dialog
        ref={dialogRef}
        id="app-mobile-navigation"
        aria-label={tx('Navigation')}
        className="app-navigation-dialog"
        onClose={() => setOpen(false)}
        onCancel={() => setOpen(false)}
        onClick={(event) => {
          if (event.target === event.currentTarget) setOpen(false);
        }}
      >
        <div className="flex flex-col h-full bg-white">
          <button
            onClick={() => setOpen(false)}
            className="self-end mx-4 mt-3 px-3 py-2 text-sm text-gray-600 rounded-lg hover:bg-gray-50"
          >
            {tx('Menü schliessen ×')}
          </button>
          <SidebarContent pathname={pathname} onNavigate={() => setOpen(false)} />
        </div>
      </dialog>
    </>
  );
}

function AppGuard({ children }: { children: React.ReactNode }) {
  const { user, loading, status, issue, refresh } = useAuth();
  const locale = useLocale();
  const t = useTranslations('Auth');
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user && status !== 'unavailable') {
      router.replace(
        status === 'signed-out'
          ? `/${locale}`
          : signInPath(locale, pathname + window.location.search, issue === 'expired')
      );
    }
  }, [user, loading, status, issue, router, locale, pathname]);

  if (loading) {
    return (
      <div
        className="min-h-screen flex items-center justify-center"
        style={{ background: 'var(--dash-bg)' }}
      >
        <div className="text-gray-400 text-sm">{t('checking')}</div>
      </div>
    );
  }

  if (status === 'unavailable') {
    return (
      <div
        className="min-h-screen flex flex-col items-center justify-center gap-4 p-6"
        role="alert"
      >
        <p>{t(`errors.${issue === 'network' ? 'network' : 'service'}`)}</p>
        <button
          onClick={() => void refresh()}
          className="rounded-apple bg-gm-green-500 px-5 py-2 text-white"
        >
          {t('retry')}
        </button>
      </div>
    );
  }
  if (!user) return null;

  return (
    <div className="app-workspace flex min-h-screen" style={{ background: 'var(--dash-bg)' }}>
      <AppSidebar />
      <MobileDrawer />
      <main className="flex-1 min-w-0 md:ml-64 pt-16 md:pt-0 relative z-10">
        <div className="max-w-[1280px] mx-auto px-4 sm:px-6 lg:px-8 py-6 lg:py-8">{children}</div>
      </main>
    </div>
  );
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return <AppGuard>{children}</AppGuard>;
}
