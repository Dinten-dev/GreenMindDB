'use client';

import { ReactNode } from 'react';
import { AuthProvider } from '@/contexts/AuthContext';
import Header from '@/components/Header';

export default function ClientLayout({ children }: { children: ReactNode }) {
    return (
        <AuthProvider>
            <Header />
            <main>{children}</main>

            {/* Footer */}
            <footer className="mt-20 py-12 bg-gray-100 border-t border-gray-200">
                <div className="max-w-6xl mx-auto px-6">
                    <div className="flex flex-col md:flex-row justify-between items-center gap-4">
                        <div className="text-gray-500 text-sm">
                            Plant Wiki — Optimal Growing Conditions Database
                        </div>
                        <div className="text-gray-400 text-xs">
                            All data sourced from university extension services and agricultural research.
                        </div>
                    </div>
                </div>
            </footer>
        </AuthProvider>
    );
}
