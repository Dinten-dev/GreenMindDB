'use client';

import { useState } from 'react';
import ScrollReveal from '@/components/ScrollReveal';
import { apiSubmitContact } from '@/lib/api';

export default function ContactPage() {
    const [formData, setFormData] = useState({
        name: '',
        email: '',
        company: '',
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
            await apiSubmitContact(formData);
            setStatus('success');
            setFormData({ name: '', email: '', company: '', message: '', website: '' });
        } catch (err: unknown) {
            setStatus('error');
            setErrorMessage(err instanceof Error ? err.message : 'Ein unerwarteter Fehler ist aufgetreten.');
        }
    };

    const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
        setFormData(prev => ({ ...prev, [e.target.name]: e.target.value }));
    };

    return (
        <div className="min-h-screen">
            <div className="pt-28 pb-24 px-6 max-w-[1280px] mx-auto">
                <div className="flex flex-col lg:flex-row gap-16">
                    <div className="flex-1 max-w-2xl">
                        <ScrollReveal>
                            <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">Kontakt</p>
                            <h1 className="text-4xl md:text-6xl font-bold text-apple-gray-800 mb-6 tracking-tight">Kontaktieren Sie uns.</h1>
                        </ScrollReveal>
                        <ScrollReveal delay={200}>
                            <p className="text-xl text-apple-gray-500 mb-16 leading-relaxed">
                                Bereit, Ihren Anbaubetrieb auf das nächste Level zu bringen? Wir möchten gerne mehr über Ihr Gewächshaus erfahren und gemeinsam erkunden, wie GreenMind helfen kann.
                            </p>
                        </ScrollReveal>

                        <ScrollReveal delay={350}>
                            <div className="bg-apple-gray-100 rounded-apple-lg p-10 border border-apple-gray-200/50">
                                <h3 className="text-sm font-semibold text-apple-gray-500 uppercase tracking-wider mb-3">Standort</h3>
                                <p className="text-lg text-apple-gray-800 leading-relaxed">
                                    FHNW Campus Brugg-Windisch<br />
                                    Brugg, Schweiz
                                </p>
                            </div>
                        </ScrollReveal>
                    </div>

                    <div className="flex-1">
                        <ScrollReveal delay={250}>
                            <div className="bg-white rounded-apple-lg shadow-apple p-10 border border-apple-gray-100">
                                {status === 'success' ? (
                                    <div className="text-center py-12">
                                        <div className="w-20 h-20 rounded-full bg-green-50 flex items-center justify-center mx-auto mb-6">
                                            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-green-500">
                                                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                                                <polyline points="22 4 12 14.01 9 11.01" />
                                            </svg>
                                        </div>
                                        <h3 className="text-2xl font-bold text-apple-gray-800 mb-4">Nachricht gesendet!</h3>
                                        <p className="text-apple-gray-500 mb-8">Vielen Dank für Ihre Nachricht. Wir werden uns in Kürze bei Ihnen melden.</p>
                                        <button
                                            onClick={() => setStatus('idle')}
                                            className="px-8 py-3 bg-apple-gray-100 text-apple-gray-800 rounded-full font-medium hover:bg-apple-gray-200 transition-colors"
                                        >
                                            Weitere Nachricht senden
                                        </button>
                                    </div>
                                ) : (
                                    <form onSubmit={handleSubmit} className="space-y-6">
                                        {status === 'error' && (
                                            <div className="p-4 bg-red-50 text-red-600 rounded-apple text-sm">
                                                {errorMessage}
                                            </div>
                                        )}
                                        {/* Honeypot field - hidden from real users */}
                                        <input type="text" name="website" value={formData.website} onChange={handleChange} className="hidden" tabIndex={-1} autoComplete="off" />

                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                            <div>
                                                <label className="block text-sm font-medium text-apple-gray-600 mb-2">Name *</label>
                                                <input required type="text" name="name" value={formData.name} onChange={handleChange} className="w-full px-4 py-3 rounded-apple bg-apple-gray-50 border border-apple-gray-200 text-apple-gray-800 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:bg-white transition-all shadow-sm" placeholder="Ihr Name" />
                                            </div>
                                            <div>
                                                <label className="block text-sm font-medium text-apple-gray-600 mb-2">Unternehmen</label>
                                                <input type="text" name="company" value={formData.company} onChange={handleChange} className="w-full px-4 py-3 rounded-apple bg-apple-gray-50 border border-apple-gray-200 text-apple-gray-800 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:bg-white transition-all shadow-sm" placeholder="Optional" />
                                            </div>
                                        </div>

                                        <div>
                                            <label className="block text-sm font-medium text-apple-gray-600 mb-2">E-Mail *</label>
                                            <input required type="email" name="email" value={formData.email} onChange={handleChange} className="w-full px-4 py-3 rounded-apple bg-apple-gray-50 border border-apple-gray-200 text-apple-gray-800 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:bg-white transition-all shadow-sm" placeholder="sie@beispiel.com" />
                                        </div>

                                        <div>
                                            <label className="block text-sm font-medium text-apple-gray-600 mb-2">Nachricht *</label>
                                            <textarea required name="message" value={formData.message} onChange={handleChange} rows={5} className="w-full px-4 py-3 rounded-apple bg-apple-gray-50 border border-apple-gray-200 text-apple-gray-800 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:bg-white transition-all shadow-sm resize-none" placeholder="Wie können wir helfen?"></textarea>
                                        </div>

                                        <button
                                            type="submit"
                                            disabled={status === 'loading'}
                                            className="w-full py-4 bg-gm-green-500 text-white rounded-apple font-medium hover:bg-gm-green-600 transition-colors disabled:opacity-70 flex justify-center items-center shadow-lg shadow-gm-green-500/20"
                                        >
                                            {status === 'loading' ? (
                                                <svg className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                                </svg>
                                            ) : 'Nachricht senden'}
                                        </button>
                                    </form>
                                )}
                            </div>
                        </ScrollReveal>
                    </div>
                </div>
            </div>
        </div>
    );
}
