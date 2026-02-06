'use client';

import { useState } from 'react';
import Modal from './Modal';
import { useAuth } from '@/contexts/AuthContext';

interface AuthModalProps {
    isOpen: boolean;
    onClose: () => void;
}

export default function AuthModal({ isOpen, onClose }: AuthModalProps) {
    const { login, signup } = useAuth();
    const [mode, setMode] = useState<'login' | 'signup'>('login');
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError(null);
        setLoading(true);

        try {
            if (mode === 'login') {
                await login(email, password);
            } else {
                await signup(email, password);
            }
            onClose();
            setEmail('');
            setPassword('');
        } catch (err) {
            setError(err instanceof Error ? err.message : 'An error occurred');
        } finally {
            setLoading(false);
        }
    };

    const isFormValid = email.length > 0 && password.length >= 8;

    return (
        <Modal isOpen={isOpen} onClose={onClose} title={mode === 'login' ? 'Login' : 'Create Account'}>
            <form onSubmit={handleSubmit} className="space-y-4">
                {error && (
                    <div className="p-3 bg-red-50 border border-red-200 text-red-600 text-sm rounded-lg">
                        {error}
                    </div>
                )}

                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                        Email
                    </label>
                    <input
                        type="email"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                        placeholder="your@email.com"
                        required
                    />
                </div>

                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                        Password
                    </label>
                    <input
                        type="password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                        placeholder="Min. 8 characters"
                        minLength={8}
                        required
                    />
                    {mode === 'signup' && password.length > 0 && password.length < 8 && (
                        <p className="mt-1 text-xs text-red-500">Password must be at least 8 characters</p>
                    )}
                </div>

                <button
                    type="submit"
                    disabled={!isFormValid || loading}
                    className="w-full py-2 px-4 bg-gray-800 text-white font-medium rounded-lg hover:bg-gray-900 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors"
                >
                    {loading ? 'Loading...' : (mode === 'login' ? 'Login' : 'Create Account')}
                </button>

                <div className="text-center text-sm text-gray-500">
                    {mode === 'login' ? (
                        <>
                            Don't have an account?{' '}
                            <button
                                type="button"
                                onClick={() => { setMode('signup'); setError(null); }}
                                className="text-blue-600 hover:underline"
                            >
                                Sign up
                            </button>
                        </>
                    ) : (
                        <>
                            Already have an account?{' '}
                            <button
                                type="button"
                                onClick={() => { setMode('login'); setError(null); }}
                                className="text-blue-600 hover:underline"
                            >
                                Login
                            </button>
                        </>
                    )}
                </div>
            </form>
        </Modal>
    );
}
