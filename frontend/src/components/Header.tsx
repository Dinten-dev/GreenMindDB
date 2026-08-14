'use client';

import { useState } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import AuthModal from './AuthModal';

export default function Header() {
    const { user, loading, logout } = useAuth();
    const [showAuthModal, setShowAuthModal] = useState(false);

    return (
        <>
            <header className="sticky top-0 z-50 backdrop-blur-xl bg-white/80 border-b border-gray-200">
                <nav className="max-w-6xl mx-auto px-6 py-4">
                    <div className="flex items-center justify-between">
                        <a href="/" className="flex items-center gap-3 hover:no-underline">
                            <span className="text-2xl">🌱</span>
                            <span className="text-xl font-semibold text-gray-800">Plant Wiki</span>
                        </a>
                        <div className="flex items-center gap-6 text-sm text-gray-500">
                            <a href="/" className="hover:text-gray-800">Plants</a>
                            <span className="text-gray-300">|</span>
                            <span className="text-gray-400">Growing Conditions Database</span>
                            <span className="text-gray-300">|</span>

                            {loading ? (
                                <span className="text-gray-400">...</span>
                            ) : user ? (
                                <div className="flex items-center gap-3">
                                    <span className="text-gray-600">{user.email}</span>
                                    <button
                                        onClick={logout}
                                        className="px-3 py-1 text-xs text-gray-500 hover:text-gray-800 bg-gray-100 hover:bg-gray-200 rounded transition-colors"
                                    >
                                        Logout
                                    </button>
                                </div>
                            ) : (
                                <button
                                    onClick={() => setShowAuthModal(true)}
                                    className="px-4 py-1.5 text-sm text-white bg-gray-800 hover:bg-gray-900 rounded-lg transition-colors"
                                >
                                    Login
                                </button>
                            )}
                        </div>
                    </div>
                </nav>
            </header>

            <AuthModal isOpen={showAuthModal} onClose={() => setShowAuthModal(false)} />
        </>
    );
}
