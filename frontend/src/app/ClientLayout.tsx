'use client';

import { ReactNode } from 'react';
import Header from '@/components/Header';

export default function ClientLayout({ children }: { children: ReactNode }) {
    return (
        <>
            <Header />
            <main>{children}</main>

            <footer className="mt-16 py-8 bg-gray-50 border-t border-gray-200">
                <div className="max-w-7xl mx-auto px-6">
                    <div className="flex flex-col md:flex-row justify-between items-center gap-3">
                        <div className="text-gray-500 text-sm">
                            🌿 GreenMindDB — Mac mini Monitoring Stack
                        </div>
                        <div className="text-gray-400 text-xs">
                            ESP32 + Raspberry Pi → TimescaleDB + MinIO
                        </div>
                    </div>
                </div>
            </footer>
        </>
    );
}
