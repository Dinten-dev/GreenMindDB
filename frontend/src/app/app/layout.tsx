'use client';

import { useEffect } from 'react';
import Link from 'next/link';
import { useRouter, usePathname } from 'next/navigation';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';

function AppSidebar() {
    const { user, logout } = useAuth();
    const pathname = usePathname();
    const router = useRouter();

    const links = [
        { href: '/app/dashboard', label: 'Dashboard', icon: '◉' },
        { href: '/app/greenhouses', label: 'Greenhouses', icon: '⌂' },
        { href: '/app/gateways', label: 'Gateways', icon: '◎' },
        { href: '/app/sensors', label: 'Sensors', icon: '◈' },
        { href: '/app/account', label: 'Account', icon: '○' },
    ];

    const handleLogout = async () => {
        await logout();
        router.push('/');
    };

    return (
        <aside className="w-60 h-screen bg-apple-gray-100/50 border-r border-apple-gray-200 flex flex-col fixed left-0 top-0 z-50">
            {/* Logo */}
            <div className="px-5 h-14 flex items-center border-b border-apple-gray-200/50">
                <Link href="/" className="flex items-center gap-2">
                    <div className="w-7 h-7 rounded-lg bg-gm-green-500 flex items-center justify-center">
                        <span className="text-white text-sm font-bold">G</span>
                    </div>
                    <span className="font-semibold text-apple-gray-800 text-sm">GreenMind</span>
                </Link>
            </div>

            {/* Navigation */}
            <nav className="flex-1 px-3 py-4 space-y-0.5">
                {links.map((link) => {
                    const isActive = pathname === link.href;
                    return (
                        <Link
                            key={link.href}
                            href={link.href}
                            className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-all duration-150 ${isActive
                                    ? 'bg-gm-green-500 text-white font-medium'
                                    : 'text-apple-gray-500 hover:bg-apple-gray-200/60 hover:text-apple-gray-800'
                                }`}
                        >
                            <span className="text-base leading-none">{link.icon}</span>
                            <span>{link.label}</span>
                        </Link>
                    );
                })}
            </nav>

            {/* User */}
            <div className="px-3 py-4 border-t border-apple-gray-200/50">
                <div className="px-3 py-2">
                    <p className="text-sm font-medium text-apple-gray-800 truncate">{user?.name || user?.email}</p>
                    <p className="text-xs text-apple-gray-400 truncate">{user?.email}</p>
                </div>
                <button
                    onClick={handleLogout}
                    className="w-full mt-2 px-3 py-2 text-sm text-apple-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all duration-150 text-left"
                >
                    Sign Out
                </button>
            </div>
        </aside>
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
            <div className="min-h-screen flex items-center justify-center bg-apple-gray-100">
                <div className="text-apple-gray-400 text-sm">Loading…</div>
            </div>
        );
    }

    if (!user) return null;

    return (
        <div className="flex min-h-screen bg-apple-gray-100">
            <AppSidebar />
            <main className="flex-1 ml-60 pt-16">
                <div className="max-w-[1280px] mx-auto px-8 py-8">
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
