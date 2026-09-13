'use client';

import { useEffect, useState, useRef } from 'react';
import Link from 'next/link';
import { useLocale } from 'next-intl';
import { useRouter, usePathname } from 'next/navigation';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';

const NAV_GROUPS = [
  {
    label: 'Dein Gewächshaus',
    links: [
      { href: '/app/dashboard', label: 'Überblick', icon: '◉' },
      { href: '/app/plants', label: 'Pflanzen', icon: '⍋' },
      { href: '/app/sensors', label: 'Messungen & Sensoren', icon: '∿' },
      { href: '/app/zones', label: 'Zonen', icon: '⌂' },
    ],
  },
  {
    label: 'Verwaltung',
    links: [
      { href: '/app/gateways', label: 'Gateways', icon: '◎' },
      { href: '/app/account', label: 'Mein Konto', icon: '○' },
      { href: '/app/firmware/dashboard', label: 'Firmware', icon: '⬡', roles: ['admin'] },
      { href: '/app/gateway-fleet/overview', label: 'Gateway-Flotte', icon: '⊞', roles: ['admin'] },
    ],
  },
];

function SidebarContent({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
  const { user, logout } = useAuth();
  const locale = useLocale();
  const localPath = pathname.replace(new RegExp(`^/${locale}(?=/|$)`), '');
  const router = useRouter();

  const handleLogout = async () => {
    await logout();
    router.push(`/${locale}`);
  };

  return (
    <>
      {/* Logo */}
      <div className="px-5 h-20 flex items-center border-b border-black/[0.04]">
        <Link
          href={`/${locale}/app/dashboard`}
          className="flex items-center gap-2.5"
          onClick={onNavigate}
        >
          <div className="w-8 h-8 rounded-xl bg-emerald-600 flex items-center justify-center shadow-sm">
            <span className="text-white text-sm font-bold">G</span>
          </div>
          <span className="font-semibold text-[15px] text-gray-800 tracking-tight">GreenMind</span>
        </Link>
      </div>

      <nav aria-label="Hauptnavigation" className="flex-1 overflow-y-auto px-3 py-5 space-y-7">
        {NAV_GROUPS.map((group) => (
          <div key={group.label}>
            <p className="px-3 mb-2 text-xs font-medium text-gray-500">{group.label}</p>
            <div className="space-y-1">
              {group.links
                .filter((link) => !('roles' in link) || (user && link.roles?.includes(user.role)))
                .map((link) => {
                  const sectionPath = link.href.replace(/\/(dashboard|overview)$/, '');
                  const isActive =
                    localPath === link.href ||
                    (link.href !== '/app/dashboard' &&
                      (localPath === sectionPath || localPath.startsWith(sectionPath + '/')));
                  return (
                    <Link
                      key={link.href}
                      href={`/${locale}${link.href}`}
                      onClick={onNavigate}
                      aria-current={isActive ? 'page' : undefined}
                      className={`flex items-center gap-3 px-3 py-3 rounded-xl text-sm transition-colors ${isActive ? 'nav-active' : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'}`}
                    >
                      <span aria-hidden="true" className="w-5 text-center text-lg leading-none">
                        {link.icon}
                      </span>
                      <span>{link.label}</span>
                    </Link>
                  );
                })}
            </div>
          </div>
        ))}
      </nav>

      {/* User */}
      <div className="px-3 py-4 border-t border-black/[0.04]">
        <div className="px-3 py-2">
          <p className="text-sm font-medium text-gray-800 truncate">{user?.name || user?.email}</p>
          <p className="text-xs text-gray-400 truncate">{user?.email}</p>
        </div>
        <button
          onClick={handleLogout}
          className="w-full mt-2 px-3 py-2 text-sm text-gray-400 hover:text-red-500 hover:bg-red-50/60 rounded-xl transition-all duration-200 text-left"
        >
          Abmelden
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
          <span aria-hidden="true">☰</span> Menü
        </button>
        <span className="ml-auto text-sm font-semibold text-emerald-700">GreenMind</span>
      </div>
      <dialog
        ref={dialogRef}
        id="app-mobile-navigation"
        aria-label="Navigation"
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
            Menü schliessen ×
          </button>
          <SidebarContent pathname={pathname} onNavigate={() => setOpen(false)} />
        </div>
      </dialog>
    </>
  );
}

function AppGuard({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const locale = useLocale();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.push(`/${locale}/login`);
    }
  }, [user, loading, router, locale]);

  if (loading) {
    return (
      <div
        className="min-h-screen flex items-center justify-center"
        style={{ background: 'var(--dash-bg)' }}
      >
        <div className="text-gray-400 text-sm">Dein Gewächshaus wird geladen…</div>
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
  return (
    <AuthProvider>
      <AppGuard>{children}</AppGuard>
    </AuthProvider>
  );
}
