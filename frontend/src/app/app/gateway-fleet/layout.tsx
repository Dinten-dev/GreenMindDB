'use client';

import Link from 'next/link';
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
    const { user, loading } = useAuth();
    const router = useRouter();
    const pathname = usePathname();

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
            <div>
                <h1 className="text-2xl font-bold text-gray-800 tracking-tight">
                    Gateway Fleet Management
                </h1>
                <p className="text-sm text-gray-400 mt-1">
                    Remote Updates, Konfiguration und Steuerung für Raspberry Pi Gateways
                </p>
            </div>

            <nav className="flex gap-1 p-1 bg-black/[0.03] rounded-2xl w-fit">
                {GATEWAY_TABS.map((tab) => {
                    const isActive =
                        pathname === tab.href ||
                        (tab.href !== '/app/gateway-fleet/overview' && pathname.startsWith(tab.href));
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

            {children}
        </div>
    );
}
