'use client';

import { useState } from 'react';
import ScrollReveal from '@/components/ScrollReveal';
import { apiSubmitEarlyAccess } from '@/lib/api';

export default function EarlyAccessPage() {
    const [formData, setFormData] = useState({
        name: '',
        company: '',
        email: '',
        country: '',
        message: '',
        website: '' // Honey pot
    });

    const [status, setStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
    const [errorMessage, setErrorMessage] = useState('');

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setStatus('loading');
        setErrorMessage('');

        try {
            await apiSubmitEarlyAccess(formData);
            setStatus('success');
            setFormData({ name: '', company: '', email: '', country: '', message: '', website: '' });
        } catch (err: unknown) {
            setStatus('error');
            setErrorMessage(err instanceof Error ? err.message : 'Ein unerwarteter Fehler ist aufgetreten.');
        }
    };

    const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
        setFormData(prev => ({ ...prev, [e.target.name]: e.target.value }));
    };

    return (
        <div className="min-h-screen">
            <div className="pt-28 pb-24 px-6 max-w-[800px] mx-auto">
                <div className="text-center mb-16">
                    <ScrollReveal variant="scale-in">
                        <div className="w-16 h-16 rounded-full bg-gm-green-50 flex items-center justify-center mx-auto mb-6">
                            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-gm-green-600">
                                <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" />
                                <polyline points="22,6 12,13 2,6" />
                            </svg>
                        </div>
                    </ScrollReveal>

                    <ScrollReveal delay={150}>
                        <h1 className="text-4xl md:text-5xl font-bold text-apple-gray-800 mb-6 tracking-tight">Zugang anfragen</h1>
                    </ScrollReveal>
                    <ScrollReveal delay={300}>
                        <p className="text-xl text-apple-gray-500 leading-relaxed">
                            GreenMind befindet sich in Entwicklung. Sie möchten die Plattform
                            testen oder Teil des Projekts werden? Wir freuen uns auf Ihre Anfrage.
                        </p>
                    </ScrollReveal>
                </div>

                <ScrollReveal delay={400}>
                    <div className="bg-white rounded-apple-lg shadow-apple p-8 md:p-12 border border-apple-gray-100">
                        {status === 'success' ? (
                            <div className="text-center py-12">
                                <div className="w-20 h-20 rounded-full bg-gm-green-50 flex items-center justify-center mx-auto mb-6">
                                    <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-gm-green-500">
                                        <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                                        <polyline points="22 4 12 14.01 9 11.01" />
                                    </svg>
                                </div>
                                <h3 className="text-3xl font-bold text-apple-gray-800 mb-4">Anfrage eingegangen!</h3>
                                <p className="text-lg text-apple-gray-500 mb-8 max-w-md mx-auto">Vielen Dank für Ihr Interesse. Wir melden uns in Kürze bei Ihnen.</p>
                                <button
                                    onClick={() => setStatus('idle')}
                                    className="px-8 py-3 bg-apple-gray-100 text-apple-gray-800 rounded-full font-medium hover:bg-apple-gray-200 transition-colors"
                                >
                                    Weitere Anfrage stellen
                                </button>
                            </div>
                        ) : (
                            <form onSubmit={handleSubmit} className="space-y-6">
                                {status === 'error' && (
                                    <div className="p-4 bg-red-50 text-red-600 rounded-apple text-sm">
                                        {errorMessage}
                                    </div>
                                )}

                                <input type="text" name="website" value={formData.website} onChange={handleChange} className="hidden" tabIndex={-1} autoComplete="off" />

                                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                    <div>
                                        <label className="block text-sm font-medium text-apple-gray-600 mb-2">Name *</label>
                                        <input required type="text" name="name" value={formData.name} onChange={handleChange} className="w-full px-4 py-3 rounded-apple bg-apple-gray-50 border border-apple-gray-200 text-apple-gray-800 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:bg-white transition-all shadow-sm" placeholder="Ihr Name" />
                                    </div>
                                    <div>
                                        <label className="block text-sm font-medium text-apple-gray-600 mb-2">E-Mail *</label>
                                        <input required type="email" name="email" value={formData.email} onChange={handleChange} className="w-full px-4 py-3 rounded-apple bg-apple-gray-50 border border-apple-gray-200 text-apple-gray-800 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:bg-white transition-all shadow-sm" placeholder="sie@beispiel.com" />
                                    </div>
                                </div>

                                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                    <div>
                                        <label className="block text-sm font-medium text-apple-gray-600 mb-2">Organisation / Betrieb *</label>
                                        <input required type="text" name="company" value={formData.company} onChange={handleChange} className="w-full px-4 py-3 rounded-apple bg-apple-gray-50 border border-apple-gray-200 text-apple-gray-800 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:bg-white transition-all shadow-sm" placeholder="Unternehmen oder Betrieb" />
                                    </div>
                                    <div>
                                        <label className="block text-sm font-medium text-apple-gray-600 mb-2">Land *</label>
                                        <select required name="country" value={formData.country} onChange={handleChange} className="w-full px-4 py-3 rounded-apple bg-apple-gray-50 border border-apple-gray-200 text-apple-gray-800 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:bg-white transition-all shadow-sm appearance-none">
                                            <option value="" disabled>Bitte wählen...</option>
                                            <option value="Schweiz">Schweiz</option>
                                            <option value="Deutschland">Deutschland</option>
                                            <option value="Österreich">Österreich</option>
                                            <option value="Andere">Andere</option>
                                        </select>
                                    </div>
                                </div>

                                <div>
                                    <label className="block text-sm font-medium text-apple-gray-600 mb-2">Was interessiert Sie?</label>
                                    <textarea name="message" value={formData.message} onChange={handleChange} rows={4} className="w-full px-4 py-3 rounded-apple bg-apple-gray-50 border border-apple-gray-200 text-apple-gray-800 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:bg-white transition-all shadow-sm resize-none" placeholder="Was bauen Sie an? Wie möchten Sie GreenMind einsetzen?"></textarea>
                                </div>

                                <div className="pt-2">
                                    <button
                                        type="submit"
                                        disabled={status === 'loading'}
                                        className="w-full py-4 bg-apple-gray-800 text-white rounded-apple font-medium hover:bg-apple-gray-900 transition-colors disabled:opacity-70 flex justify-center items-center shadow-lg shadow-apple-gray-800/20"
                                    >
                                        {status === 'loading' ? (
                                            <svg className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                            </svg>
                                        ) : 'Zugang anfragen'}
                                    </button>
                                </div>
                            </form>
                        )}
                    </div>
                </ScrollReveal>
            </div>
        </div>
    );
}
