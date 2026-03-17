'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { apiLogin } from '@/lib/api';

export default function LoginPage() {
    const router = useRouter();
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setLoading(true);
        try {
            await apiLogin(email, password);
            router.push('/app/dashboard');
        } catch (err: any) {
            setError(err.message || 'Login failed');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="min-h-screen flex items-center justify-center px-6 bg-apple-gray-100">
            <div className="w-full max-w-sm">
                {/* Logo */}
                <div className="text-center mb-8">
                    <Link href="/" className="inline-flex items-center gap-2 mb-6">
                        <div className="w-10 h-10 rounded-xl bg-gm-green-500 flex items-center justify-center">
                            <span className="text-white text-xl font-bold">G</span>
                        </div>
                    </Link>
                    <h1 className="text-2xl font-bold text-apple-gray-800">Sign in to GreenMind</h1>
                    <p className="text-sm text-apple-gray-400 mt-2">Welcome back. Enter your credentials.</p>
                </div>

                {/* Form */}
                <div className="bg-white rounded-apple-lg shadow-apple p-8">
                    <form onSubmit={handleSubmit} className="space-y-4">
                        {error && (
                            <div className="px-4 py-3 rounded-apple bg-red-50 text-red-600 text-sm">
                                {error}
                            </div>
                        )}
                        <div>
                            <label className="block text-sm font-medium text-apple-gray-600 mb-1.5">Email</label>
                            <input
                                type="email"
                                required
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                className="w-full px-4 py-2.5 rounded-apple bg-apple-gray-100 border border-apple-gray-200 text-apple-gray-800 placeholder-apple-gray-400 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:border-transparent transition-all text-sm"
                                placeholder="you@example.com"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-apple-gray-600 mb-1.5">Password</label>
                            <input
                                type="password"
                                required
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                className="w-full px-4 py-2.5 rounded-apple bg-apple-gray-100 border border-apple-gray-200 text-apple-gray-800 placeholder-apple-gray-400 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:border-transparent transition-all text-sm"
                                placeholder="Enter your password"
                            />
                        </div>
                        <button
                            type="submit"
                            disabled={loading}
                            className="w-full py-2.5 bg-gm-green-500 text-white rounded-apple font-medium hover:bg-gm-green-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-sm"
                        >
                            {loading ? 'Signing in…' : 'Sign In'}
                        </button>
                    </form>
                </div>

                <p className="text-center text-sm text-apple-gray-400 mt-6">
                    Don't have an account?{' '}
                    <Link href="mailto:traver.dinten@outlook.com?subject=Early Access Request - GreenMind&body=Name:%0D%0ACompany:%0D%0A%0D%0ATell us about your greenhouse operation:" className="text-gm-green-500 font-medium hover:text-gm-green-600 transition-colors">
                        Create one
                    </Link>
                </p>
            </div>
        </div>
    );
}
