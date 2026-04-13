'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useAuth } from '@/contexts/AuthContext';
import { useEffect } from 'react';

const FIRMWARE_TABS = [
    { href: '/app/firmware/dashboard', label: 'Dashboard' },
    { href: '/app/firmware/releases', label: 'Releases' },
    { href: '/app/firmware/rollouts', label: 'Rollouts' },
    { href: '/app/firmware/reports', label: 'Reports' },
    { href: '/app/firmware/audit', label: 'Audit Log' },
];

export default function FirmwareLayout({ children }: { children: React.ReactNode }) {
    const { user, loading } = useAuth();
    const router = useRouter();
    const pathname = usePathname();

    // Role guard: only admin/owner can access firmware management
    useEffect(() => {
        if (!loading && user && !['admin', 'owner'].includes(user.role)) {
            router.push('/app/dashboard');
        }
    }, [user, loading, router]);

    if (loading) {
        return (
            <div className="animate-pulse space-y-4">
                <div className="h-8 w-48 bg-black/[0.04] rounded-xl" />
                <div className="h-64 bg-black/[0.04] rounded-2xl" />
            </div>
        );
    }

    if (!user || !['admin', 'owner'].includes(user.role)) {
        return null;
    }

    return (
        <div className="space-y-6">
            {/* Header */}
            <div>
                <h1 className="text-2xl font-bold text-gray-800 tracking-tight">
                    Firmware Management
                </h1>
                <p className="text-sm text-gray-400 mt-1">
                    OTA Updates, Rollouts und Audit für ESP32 Devices
                </p>
            </div>

            {/* Tab Navigation */}
            <nav className="flex gap-1 p-1 bg-black/[0.03] rounded-2xl w-fit">
                {FIRMWARE_TABS.map((tab) => {
                    const isActive =
                        pathname === tab.href ||
                        (tab.href !== '/app/firmware/dashboard' && pathname.startsWith(tab.href));
                    return (
                        <Link
                            key={tab.href}
                            href={tab.href}
                            className={`px-4 py-2 rounded-xl text-sm font-medium transition-all duration-200 ${
                                isActive
                                    ? 'bg-white text-gray-800 shadow-sm'
                                    : 'text-gray-500 hover:text-gray-700'
                            }`}
                        >
                            {tab.label}
                        </Link>
                    );
                })}
            </nav>

            {/* Page content */}
            {children}
        </div>
    );
}
