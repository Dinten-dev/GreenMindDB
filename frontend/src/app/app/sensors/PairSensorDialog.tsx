'use client';

import { useState, useEffect } from 'react';
import { apiListZones, Zone } from '@/lib/api';

interface PairSensorDialogProps {
    isOpen: boolean;
    onClose: () => void;
    onSuccess: () => void;
}

export default function PairSensorDialog({ isOpen, onClose, onSuccess }: PairSensorDialogProps) {
    const [zones, setZones] = useState<Zone[]>([]);
    const [selectedZone, setSelectedZone] = useState<string>('');
    const [pairingCode, setPairingCode] = useState<string | null>(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (isOpen) {
            setPairingCode(null);
            setError('');
            apiListZones().then(z => {
                setZones(z);
                if (z.length > 0) setSelectedZone(z[0].id);
            });
        }
    }, [isOpen]);

    if (!isOpen) return null;

    const handleGenerate = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        
        if (!selectedZone) {
            setError('Bitte wähle eine Zone.');
            return;
        }

        setLoading(true);
        try {
            const res = await fetch('/api/v1/sensors/pairing-code', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ zone_id: selectedZone })
            });

            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.detail || 'Code konnte nicht generiert werden');
            }

            const data = await res.json();
            setPairingCode(data.code);
        } catch (err: unknown) {
            if (err instanceof Error) {
                setError(err.message);
            } else {
                setError('Ein unbekannter Fehler ist aufgetreten');
            }
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
            <div className="absolute inset-0 bg-black/20 backdrop-blur-md transition-opacity" onClick={onClose} />
            
            <div className="relative bg-white/80 rounded-[2rem] border border-white/50 shadow-2xl backdrop-blur-xl p-8 max-w-md w-full animate-in fade-in zoom-in duration-300">
                <div className="w-16 h-16 rounded-2xl bg-gradient-to-tr from-emerald-100 to-teal-50 text-emerald-500 flex items-center justify-center mb-6 mx-auto shadow-sm border border-emerald-100/50">
                    <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
                    </svg>
                </div>
                
                <h3 className="text-2xl font-bold text-center text-gray-800 tracking-tight mb-2">Sensor Hinzufügen</h3>
                
                {!pairingCode ? (
                    <>
                        <p className="text-sm text-center text-gray-500 mb-8 leading-relaxed">
                            Wähle die Ziel-Zone aus, um einen temporären Pairing-Code zu erstellen.
                        </p>
                        <form onSubmit={handleGenerate} className="space-y-6">
                            <div>
                                <label className="block text-xs font-semibold text-gray-600 uppercase tracking-wider mb-2 ml-1">Zone (Gewächshaus)</label>
                                <select
                                    value={selectedZone}
                                    onChange={(e) => setSelectedZone(e.target.value)}
                                    className="w-full bg-white/60 border border-black/5 rounded-2xl px-5 py-3.5 text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30 appearance-none transition-shadow cursor-pointer"
                                >
                                    {zones.map(z => (
                                        <option key={z.id} value={z.id}>{z.name}</option>
                                    ))}
                                </select>
                            </div>

                            {error && (
                                <div className="p-3 bg-red-50/50 border border-red-100 rounded-xl text-red-600 text-sm text-center animate-in fade-in">
                                    {error}
                                </div>
                            )}

                            <div className="flex gap-4 pt-2">
                                <button
                                    type="button"
                                    onClick={onClose}
                                    className="flex-1 px-6 py-4 bg-white/40 border border-black/[0.04] text-gray-700 rounded-2xl font-medium hover:bg-white/60 transition-all hover:shadow-sm"
                                >
                                    Abbrechen
                                </button>
                                <button
                                    type="submit"
                                    disabled={loading || !selectedZone}
                                    className="flex-1 relative px-6 py-4 bg-emerald-500 text-white rounded-2xl font-medium hover:bg-emerald-600 transition-all shadow-[0_8px_16px_rgba(16,185,129,0.2)] hover:shadow-[0_8px_24px_rgba(16,185,129,0.3)] hover:-translate-y-0.5 disabled:opacity-50 overflow-hidden"
                                >
                                    <div className="relative z-10 flex items-center justify-center gap-2">
                                        {loading ? <span>Laden…</span> : <span>Code Erstellen</span>}
                                    </div>
                                </button>
                            </div>
                        </form>
                    </>
                ) : (
                    <div className="space-y-6 text-center animate-in fade-in zoom-in slide-in-from-bottom-2 duration-300">
                        <div className="bg-emerald-50/50 border-2 border-emerald-100 rounded-2xl p-6">
                            <h4 className="text-xs font-bold text-emerald-600 uppercase tracking-widest mb-2">Dein Pairing Code</h4>
                            <div className="text-4xl font-mono tracking-[0.2em] text-emerald-900 font-bold">
                                {pairingCode}
                            </div>
                        </div>

                        <ul className="text-left text-sm text-gray-600 space-y-3 bg-gray-50 p-4 rounded-xl border border-gray-100">
                            <li className="flex items-start gap-2">
                                <span className="bg-white text-emerald-600 rounded-full w-5 h-5 flex items-center justify-center font-bold shadow-sm shrink-0">1</span>
                                <span>Verbinde dein Smartphone mit dem WLAN <strong className="text-gray-900">GreenMind-Sensor-XXXX</strong></span>
                            </li>
                            <li className="flex items-start gap-2">
                                <span className="bg-white text-emerald-600 rounded-full w-5 h-5 flex items-center justify-center font-bold shadow-sm shrink-0">2</span>
                                <span>Ein Anmeldefenster öffnet sich automatisch (Captive Portal).</span>
                            </li>
                            <li className="flex items-start gap-2">
                                <span className="bg-white text-emerald-600 rounded-full w-5 h-5 flex items-center justify-center font-bold shadow-sm shrink-0">3</span>
                                <span>Trage dort dein WLAN-Passwort und den Code oben ein.</span>
                            </li>
                        </ul>

                        <button
                            onClick={onSuccess}
                            className="w-full relative px-6 py-4 bg-gray-900 text-white rounded-2xl font-medium hover:bg-gray-800 transition-all shadow-md hover:-translate-y-0.5"
                        >
                            Fertig, Code eingetragen!
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
