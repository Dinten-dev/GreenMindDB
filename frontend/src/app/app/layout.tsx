'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter, usePathname } from 'next/navigation';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';

const NAV_LINKS = [
    { href: '/app/dashboard', label: 'Dashboard', icon: '◉' },
    { href: '/app/zones', label: 'Zonen', icon: '⌂' },
    { href: '/app/gateways', label: 'Gateways', icon: '◎' },
    { href: '/app/sensors', label: 'Sensoren', icon: '◈' },
    { href: '/app/firmware/dashboard', label: 'Firmware', icon: '⬡', roles: ['admin', 'owner'] },
    { href: '/app/gateway-fleet/overview', label: 'Gateway Fleet', icon: '⊞', roles: ['admin', 'owner'] },
    { href: '/app/account', label: 'Account', icon: '○' },
];

function SidebarContent({ pathname, onNavigate }: { pathname: string; onNavigate?: () => void }) {
    const { user, logout } = useAuth();
    const router = useRouter();

    const handleLogout = async () => {
        await logout();
        router.push('/');
    };

    return (
        <>
            {/* Logo */}
            <div className="px-5 h-14 flex items-center border-b border-black/[0.04]">
                <Link href="/" className="flex items-center gap-2.5" onClick={onNavigate}>
                    <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-emerald-400 to-emerald-600 flex items-center justify-center shadow-sm">
                        <span className="text-white text-sm font-bold">G</span>
                    </div>
                    <span className="font-semibold text-[15px] text-gray-800 tracking-tight">GreenMind</span>
                </Link>
            </div>

            {/* Navigation */}
            <nav className="flex-1 px-3 py-4 space-y-0.5">
                {NAV_LINKS.filter((link) => {
                    if ('roles' in link && link.roles) {
                        return user && link.roles.includes(user.role);
                    }
                    return true;
                }).map((link) => {
                    const isActive = pathname === link.href || (link.href !== '/app/dashboard' && pathname.startsWith(link.href.replace('/dashboard', '')));
                    return (
                        <Link
                            key={link.href}
                            href={link.href}
                            onClick={onNavigate}
                            className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-[13px] transition-all duration-200 ${
                                isActive
                                    ? 'nav-active'
                                    : 'text-gray-500 hover:bg-black/[0.03] hover:text-gray-700'
                            }`}
                        >
                            <span className="text-base leading-none">{link.icon}</span>
                            <span>{link.label}</span>
                        </Link>
                    );
                })}
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
        <aside className="glass-sidebar w-60 h-screen flex-col fixed left-0 top-0 z-30 hidden md:flex">
            <SidebarContent pathname={pathname} />
        </aside>
    );
}

function MobileDrawer() {
    const [open, setOpen] = useState(false);
    const pathname = usePathname();

    // Close drawer on route change
    useEffect(() => {
        setOpen(false);
    }, [pathname]);

    return (
        <>
            {/* Hamburger Button */}
            <button
                onClick={() => setOpen(true)}
                className="fixed top-3 left-3 z-50 md:hidden p-2.5 rounded-xl glass-card"
                aria-label="Menü öffnen"
            >
                <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                    <line x1="3" y1="5" x2="17" y2="5" />
                    <line x1="3" y1="10" x2="17" y2="10" />
                    <line x1="3" y1="15" x2="17" y2="15" />
                </svg>
            </button>

            {/* Overlay */}
            <div
                className={`drawer-overlay ${open ? 'open' : ''}`}
                onClick={() => setOpen(false)}
            />

            {/* Drawer Panel */}
            <div className={`drawer-panel glass-sidebar ${open ? 'open' : ''}`}>
                <SidebarContent pathname={pathname} onNavigate={() => setOpen(false)} />
            </div>
        </>
    );
}

function LiquidBackground() {
    return (
        <div className="liquid-bg">
            <div className="liquid-blob liquid-blob-1" />
            <div className="liquid-blob liquid-blob-2" />
            <div className="liquid-blob liquid-blob-3" />
        </div>
    );
}

function AppGuard({ children }: { children: React.ReactNode }) {
    const { user, loading } = useAuth();
    const router = useRouter();

    useEffect(() => {
        if (!loading && !user) {
            router.push('/login');
        }
    }, [user, loading, router]);

    if (loading) {
        return (
            <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--dash-bg)' }}>
                <div className="text-gray-400 text-sm">Lade…</div>
            </div>
        );
    }

    if (!user) return null;

    return (
        <div className="flex min-h-screen" style={{ background: 'var(--dash-bg)' }}>
            <LiquidBackground />
            <AppSidebar />
            <MobileDrawer />
            <main className="flex-1 md:ml-60 pt-16 md:pt-0 relative z-10">
                <div className="max-w-[1280px] mx-auto px-4 sm:px-6 lg:px-8 py-6 lg:py-8">
                    {children}
                </div>
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
