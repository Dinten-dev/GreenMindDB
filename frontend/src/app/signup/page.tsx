'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { apiSignup } from '@/lib/api';

export default function SignupPage() {
    const router = useRouter();
    const [name, setName] = useState('');
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setLoading(true);
        try {
            await apiSignup(email, password, name);
            router.push('/app/dashboard');
        } catch (err: any) {
            setError(err.message || 'Registrierung fehlgeschlagen');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="min-h-screen flex items-center justify-center px-6 bg-apple-gray-100">
            <div className="w-full max-w-sm">
                <div className="text-center mb-8">
                    <Link href="/" className="inline-flex items-center gap-2 mb-6">
                        <div className="w-10 h-10 rounded-xl bg-gm-green-500 flex items-center justify-center">
                            <span className="text-white text-xl font-bold">G</span>
                        </div>
                    </Link>
                    <h1 className="text-2xl font-bold text-apple-gray-800">Konto erstellen</h1>
                    <p className="text-sm text-apple-gray-400 mt-2">Beginnen Sie noch heute mit der Überwachung Ihres Gewächshauses.</p>
                </div>

                <div className="bg-white rounded-apple-lg shadow-apple p-8">
                    <form onSubmit={handleSubmit} className="space-y-4">
                        {error && (
                            <div className="px-4 py-3 rounded-apple bg-red-50 text-red-600 text-sm">
                                {error}
                            </div>
                        )}
                        <div>
                            <label className="block text-sm font-medium text-apple-gray-600 mb-1.5">Name</label>
                            <input
                                type="text"
                                required
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                                className="w-full px-4 py-2.5 rounded-apple bg-apple-gray-100 border border-apple-gray-200 text-apple-gray-800 placeholder-apple-gray-400 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:border-transparent transition-all text-sm"
                                placeholder="Ihr Name"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-apple-gray-600 mb-1.5">E-Mail</label>
                            <input
                                type="email"
                                required
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                className="w-full px-4 py-2.5 rounded-apple bg-apple-gray-100 border border-apple-gray-200 text-apple-gray-800 placeholder-apple-gray-400 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:border-transparent transition-all text-sm"
                                placeholder="sie@beispiel.com"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-apple-gray-600 mb-1.5">Passwort</label>
                            <input
                                type="password"
                                required
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                className="w-full px-4 py-2.5 rounded-apple bg-apple-gray-100 border border-apple-gray-200 text-apple-gray-800 placeholder-apple-gray-400 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:border-transparent transition-all text-sm"
                                placeholder="Mind. 8 Zeichen, Gross- + Kleinbuchstaben + Zahl"
                            />
                        </div>
                        <button
                            type="submit"
                            disabled={loading}
                            className="w-full py-2.5 bg-gm-green-500 text-white rounded-apple font-medium hover:bg-gm-green-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-sm"
                        >
                            {loading ? 'Konto wird erstellt…' : 'Konto erstellen'}
                        </button>
                    </form>
                </div>

                <p className="text-center text-sm text-apple-gray-400 mt-6">
                    Bereits ein Konto?{' '}
                    <Link href="/login" className="text-gm-green-500 font-medium hover:text-gm-green-600 transition-colors">
                        Anmelden
                    </Link>
                </p>
            </div>
        </div>
    );
}
