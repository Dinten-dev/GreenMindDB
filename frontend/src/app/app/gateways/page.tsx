'use client';

import { useState, useEffect } from 'react';
import { apiListGateways, apiListGreenhouses, apiGeneratePairingCode, apiDeleteGateway, GatewayInfo, Greenhouse, PairingCode } from '@/lib/api';

export default function GatewaysPage() {
    const [gateways, setGateways] = useState<GatewayInfo[]>([]);
    const [greenhouses, setGreenhouses] = useState<Greenhouse[]>([]);
    const [loading, setLoading] = useState(true);

    // Pairing
    const [showPairing, setShowPairing] = useState(false);
    const [selectedGreenhouse, setSelectedGreenhouse] = useState('');
    const [pairingCode, setPairingCode] = useState<PairingCode | null>(null);
    const [generating, setGenerating] = useState(false);

    // Deletion
    const [deletingGatewayId, setDeletingGatewayId] = useState<string | null>(null);
    const [isDeleting, setIsDeleting] = useState(false);

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const [gws, ghs] = await Promise.all([
                apiListGateways(),
                apiListGreenhouses(),
            ]);
            setGateways(gws);
            setGreenhouses(ghs);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const handleGenerateCode = async () => {
        if (!selectedGreenhouse) return;
        setGenerating(true);
        try {
            const code = await apiGeneratePairingCode(selectedGreenhouse);
            setPairingCode(code);
        } catch (err) {
            console.error(err);
        } finally {
            setGenerating(false);
        }
    };

    const confirmDeleteGateway = async () => {
        if (!deletingGatewayId) return;
        setIsDeleting(true);
        try {
            await apiDeleteGateway(deletingGatewayId);
            setGateways(prev => prev.filter(g => g.id !== deletingGatewayId));
            setDeletingGatewayId(null);
        } catch (err) {
            console.error('Failed to delete gateway:', err);
        } finally {
            setIsDeleting(false);
        }
    };

    if (loading) {
        return (
            <div className="animate-pulse space-y-4">
                <div className="h-8 w-40 bg-black/[0.04] rounded-xl" />
                <div className="h-32 bg-black/[0.04] rounded-2xl" />
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-gray-800 tracking-tight">Gateways</h1>
                    <p className="text-sm text-gray-400 mt-1">Raspberry Pi Gateways verwalten</p>
                </div>
                <button
                    onClick={() => { setShowPairing(true); setPairingCode(null); }}
                    disabled={greenhouses.length === 0}
                    className="px-4 py-2 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-full text-sm font-medium hover:from-emerald-600 hover:to-emerald-700 transition-all duration-200 shadow-sm disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                >
                    + Gateway pairen
                </button>
            </div>

            {/* Pairing Modal */}
            {showPairing && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
                    <div className="glass-card p-8 w-full max-w-md mx-4 shadow-xl">
                        <h2 className="text-xl font-semibold text-gray-800 mb-6">Gateway pairen</h2>

                        {!pairingCode ? (
                            <div className="space-y-4">
                                <div>
                                    <label className="block text-sm font-medium text-gray-600 mb-1.5">Gewächshaus</label>
                                    <select
                                        value={selectedGreenhouse}
                                        onChange={(e) => setSelectedGreenhouse(e.target.value)}
                                        className="w-full px-4 py-2.5 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30 backdrop-blur-sm"
                                    >
                                        <option value="">Gewächshaus wählen…</option>
                                        {greenhouses.map(gh => (
                                            <option key={gh.id} value={gh.id}>{gh.name}</option>
                                        ))}
                                    </select>
                                </div>
                                <div className="flex gap-3 pt-2">
                                    <button
                                        type="button"
                                        onClick={() => setShowPairing(false)}
                                        className="flex-1 py-2.5 bg-black/[0.04] text-gray-600 rounded-xl text-sm font-medium hover:bg-black/[0.06] transition-colors"
                                    >
                                        Abbrechen
                                    </button>
                                    <button
                                        onClick={handleGenerateCode}
                                        disabled={!selectedGreenhouse || generating}
                                        className="flex-1 py-2.5 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-xl text-sm font-medium hover:from-emerald-600 hover:to-emerald-700 transition-all duration-200 disabled:opacity-50 shadow-sm"
                                    >
                                        {generating ? 'Generiere…' : 'Code erzeugen'}
                                    </button>
                                </div>
                            </div>
                        ) : (
                            <div className="text-center space-y-4">
                                <p className="text-sm text-gray-400">Gib diesen Code auf dem Raspberry Pi ein:</p>
                                <div className="bg-white/40 rounded-2xl py-6 px-4 border border-black/[0.04] backdrop-blur-sm">
                                    <p className="text-4xl font-mono font-bold text-gray-800 tracking-[0.3em]">
                                        {pairingCode.code}
                                    </p>
                                </div>
                                <p className="text-xs text-gray-400">
                                    Gültig bis {new Date(pairingCode.expires_at).toLocaleTimeString('de-CH')} (10 Minuten)
                                </p>
                                <button
                                    onClick={() => { setShowPairing(false); loadData(); }}
                                    className="w-full py-2.5 bg-black/[0.04] text-gray-600 rounded-xl text-sm font-medium hover:bg-black/[0.06] transition-colors"
                                >
                                    Schliessen
                                </button>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Gateway List */}
            {gateways.length === 0 ? (
                <div className="glass-card p-12 text-center">
                    <div className="text-4xl mb-4">🖥</div>
                    <h3 className="text-lg font-semibold text-gray-800 mb-2">Keine Gateways</h3>
                    <p className="text-sm text-gray-400 mb-4">
                        Verbinde dein erstes Raspberry Pi Gateway mit einem Gewächshaus.
                    </p>
                </div>
            ) : (
                <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {gateways.map(gw => (
                        <div key={gw.id} className="glass-card accent-glow p-6">
                            <div className="flex items-start justify-between mb-3">
                                <div>
                                    <h3 className="text-base font-semibold text-gray-800">{gw.name || gw.hardware_id}</h3>
                                    <p className="text-xs text-gray-400 font-mono mt-0.5">{gw.hardware_id}</p>
                                </div>
                                <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
                                    gw.status === 'online'
                                        ? 'bg-emerald-50 text-emerald-600'
                                        : 'bg-gray-100 text-gray-400'
                                }`}>
                                    <span className={`w-1.5 h-1.5 rounded-full ${gw.status === 'online' ? 'bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.4)]' : 'bg-gray-300'}`} />
                                    {gw.status}
                                </span>
                                <button
                                    onClick={(e) => { e.preventDefault(); setDeletingGatewayId(gw.id); }}
                                    className="ml-2 p-1.5 text-gray-300 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all shrink-0"
                                    title="Gateway entfernen"
                                >
                                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                    </svg>
                                </button>
                            </div>

                            <div className="grid grid-cols-3 gap-2 text-sm">
                                <div className="bg-white/40 rounded-xl px-2.5 py-1.5 border border-black/[0.03]">
                                    <p className="text-gray-400 text-xs">Sensoren</p>
                                    <p className="font-semibold text-gray-800">{gw.sensor_count}</p>
                                </div>
                                <div className="bg-white/40 rounded-xl px-2.5 py-1.5 border border-black/[0.03]">
                                    <p className="text-gray-400 text-xs">IP</p>
                                    <p className="font-semibold text-gray-800 text-xs font-mono truncate">{gw.local_ip || '–'}</p>
                                </div>
                                <div className="bg-white/40 rounded-xl px-2.5 py-1.5 border border-black/[0.03]">
                                    <p className="text-gray-400 text-xs">Gewächshaus</p>
                                    <p className="font-semibold text-gray-800 text-xs truncate">{gw.greenhouse_name || '–'}</p>
                                </div>
                            </div>

                            {gw.last_seen && (
                                <p className="text-xs text-gray-400 mt-3">
                                    Zuletzt gesehen: {new Date(gw.last_seen).toLocaleString('de-CH')}
                                </p>
                            )}
                        </div>
                    ))}
                </div>
            )}

            {/* Delete Gateway Confirmation Modal */}
            {deletingGatewayId && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
                    <div className="absolute inset-0 bg-black/20 backdrop-blur-sm" onClick={() => setDeletingGatewayId(null)} />
                    <div className="relative bg-white/80 rounded-2xl border border-white/50 shadow-2xl backdrop-blur-xl p-6 max-w-sm w-full animate-in fade-in zoom-in duration-200">
                        <div className="w-12 h-12 rounded-full bg-red-50 text-red-500 flex items-center justify-center mb-4 mx-auto">
                            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                        </div>
                        <h3 className="text-lg font-semibold text-center text-gray-800 mb-2">Gateway entfernen?</h3>
                        <p className="text-sm text-center text-gray-500 mb-6">
                            Damit wird auch die Verbindung aller zugehörigen Sensoren getrennt. Die Sensordaten werden ebenfalls gelöscht.
                        </p>
                        <div className="flex gap-3">
                            <button
                                onClick={() => setDeletingGatewayId(null)}
                                className="flex-1 px-4 py-2.5 bg-white border border-gray-200 text-gray-700 rounded-xl text-sm font-medium hover:bg-gray-50 transition-colors"
                            >
                                Abbrechen
                            </button>
                            <button
                                onClick={confirmDeleteGateway}
                                disabled={isDeleting}
                                className="flex-1 px-4 py-2.5 bg-red-500 text-white rounded-xl text-sm font-medium hover:bg-red-600 transition-colors disabled:opacity-50"
                            >
                                {isDeleting ? 'Lösche…' : 'Löschen'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
