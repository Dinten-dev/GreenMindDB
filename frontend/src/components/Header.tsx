'use client';

import { useState, useEffect } from 'react';
import { fetchHealth, HealthStatus } from '@/lib/api';

export default function Header() {
    const [health, setHealth] = useState<HealthStatus | null>(null);

    useEffect(() => {
        const poll = async () => {
            try {
                setHealth(await fetchHealth());
            } catch {
                setHealth(null);
            }
        };
        poll();
        const id = setInterval(poll, 15_000);
        return () => clearInterval(id);
    }, []);

    const isOk = health?.status === 'ok';

    return (
        <header className="sticky top-0 z-50 backdrop-blur-xl bg-white/80 border-b border-gray-200">
            <nav className="max-w-7xl mx-auto px-6 py-4">
                <div className="flex items-center justify-between">
                    <a href="/" className="flex items-center gap-3 hover:no-underline">
                        <span className="text-2xl">🌿</span>
                        <span className="text-xl font-semibold text-gray-800">GreenMindDB</span>
                        <span className="text-xs text-gray-400 hidden sm:inline">Mac mini Dashboard</span>
                    </a>

                    <div className="flex items-center gap-4 text-sm">
                        {health ? (
                            <div className="flex items-center gap-2">
                                <span className={`inline-block w-2.5 h-2.5 rounded-full ${isOk ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`} />
                                <span className={isOk ? 'text-green-700' : 'text-red-600'}>
                                    {isOk ? 'All systems ok' : 'Degraded'}
                                </span>
                            </div>
                        ) : (
                            <div className="flex items-center gap-2">
                                <span className="inline-block w-2.5 h-2.5 rounded-full bg-gray-300" />
                                <span className="text-gray-400">Connecting…</span>
                            </div>
                        )}

                        <a
                            href={`${typeof window !== 'undefined' ? window.location.protocol + '//' + window.location.hostname : 'http://localhost'}:8000/docs`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="px-3 py-1.5 text-xs text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
                        >
                            API Docs ↗
                        </a>
                    </div>
                </div>
            </nav>
        </header>
    );
}
