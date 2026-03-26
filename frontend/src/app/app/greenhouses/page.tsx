'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { apiListGreenhouses, apiCreateGreenhouse, apiDeleteGreenhouse, Greenhouse } from '@/lib/api';

export default function GreenhousesPage() {
    const [greenhouses, setGreenhouses] = useState<Greenhouse[]>([]);
    const [loading, setLoading] = useState(true);
    const [showCreate, setShowCreate] = useState(false);
    const [name, setName] = useState('');
    const [location, setLocation] = useState('');
    const [creating, setCreating] = useState(false);

    // Deletion
    const [deletingGreenhouseId, setDeletingGreenhouseId] = useState<string | null>(null);
    const [isDeleting, setIsDeleting] = useState(false);

    useEffect(() => {
        loadGreenhouses();
    }, []);

    const loadGreenhouses = async () => {
        try {
            const data = await apiListGreenhouses();
            setGreenhouses(data);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const handleCreate = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!name.trim()) return;
        setCreating(true);
        try {
            await apiCreateGreenhouse(name, location || undefined);
            setName('');
            setLocation('');
            setShowCreate(false);
            await loadGreenhouses();
        } catch (err) {
            console.error(err);
        } finally {
            setCreating(false);
        }
    };

    const confirmDeleteGreenhouse = async () => {
        if (!deletingGreenhouseId) return;
        setIsDeleting(true);
        try {
            await apiDeleteGreenhouse(deletingGreenhouseId);
            setGreenhouses(prev => prev.filter(gh => gh.id !== deletingGreenhouseId));
            setDeletingGreenhouseId(null);
        } catch (err) {
            console.error('Failed to delete greenhouse:', err);
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
                    <h1 className="text-2xl font-bold text-gray-800 tracking-tight">Gewächshäuser</h1>
                    <p className="text-sm text-gray-400 mt-1">Verwalte deine Anbauanlagen</p>
                </div>
                <button
                    onClick={() => setShowCreate(true)}
                    className="px-4 py-2 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-full text-sm font-medium hover:from-emerald-600 hover:to-emerald-700 transition-all duration-200 shadow-sm whitespace-nowrap"
                >
                    + Neues Gewächshaus
                </button>
            </div>

            {/* Create Modal */}
            {showCreate && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
                    <div className="glass-card p-8 w-full max-w-md mx-4 shadow-xl">
                        <h2 className="text-xl font-semibold text-gray-800 mb-6">Neues Gewächshaus</h2>
                        <form onSubmit={handleCreate} className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-gray-600 mb-1.5">Name</label>
                                <input
                                    type="text"
                                    required
                                    value={name}
                                    onChange={(e) => setName(e.target.value)}
                                    className="w-full px-4 py-2.5 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-500/30 backdrop-blur-sm"
                                    placeholder="z.B. Hauptgewächshaus"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-gray-600 mb-1.5">Standort (optional)</label>
                                <input
                                    type="text"
                                    value={location}
                                    onChange={(e) => setLocation(e.target.value)}
                                    className="w-full px-4 py-2.5 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-500/30 backdrop-blur-sm"
                                    placeholder="z.B. Brugg, Schweiz"
                                />
                            </div>
                            <div className="flex gap-3 pt-2">
                                <button
                                    type="button"
                                    onClick={() => setShowCreate(false)}
                                    className="flex-1 py-2.5 bg-black/[0.04] text-gray-600 rounded-xl text-sm font-medium hover:bg-black/[0.06] transition-colors"
                                >
                                    Abbrechen
                                </button>
                                <button
                                    type="submit"
                                    disabled={creating}
                                    className="flex-1 py-2.5 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-xl text-sm font-medium hover:from-emerald-600 hover:to-emerald-700 transition-all duration-200 disabled:opacity-50 shadow-sm"
                                >
                                    {creating ? 'Erstelle…' : 'Erstellen'}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* Greenhouse List */}
            {greenhouses.length === 0 ? (
                <div className="glass-card p-12 text-center">
                    <div className="text-4xl mb-4">🏠</div>
                    <h3 className="text-lg font-semibold text-gray-800 mb-2">Noch keine Gewächshäuser</h3>
                    <p className="text-sm text-gray-400 mb-4">
                        Erstelle dein erstes Gewächshaus, um Gateways und Sensoren hinzuzufügen.
                    </p>
                    <button
                        onClick={() => setShowCreate(true)}
                        className="px-6 py-2.5 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-full text-sm font-medium hover:from-emerald-600 hover:to-emerald-700 transition-all duration-200 shadow-sm"
                    >
                        Gewächshaus erstellen
                    </button>
                </div>
            ) : (
                <div className="space-y-4">
                    {greenhouses.map(gh => (
                        <Link
                            key={gh.id}
                            href={`/app/greenhouses/${gh.id}`}
                            className="block glass-card p-6 transition-all duration-300 hover:shadow-lg"
                        >
                            <div className="flex items-start justify-between">
                                <div>
                                    <h3 className="text-lg font-semibold text-gray-800 mb-1">{gh.name}</h3>
                                    {gh.location && <p className="text-sm text-gray-400">{gh.location}</p>}
                                </div>
                                <div className="flex items-center gap-2">
                                    <button
                                        onClick={(e) => { e.preventDefault(); setDeletingGreenhouseId(gh.id); }}
                                        className="p-1.5 text-gray-300 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all shrink-0"
                                        title="Gewächshaus löschen"
                                    >
                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                        </svg>
                                    </button>
                                    <span className="text-gray-400 text-lg">›</span>
                                </div>
                            </div>
                            <div className="grid grid-cols-2 gap-3 text-sm mt-4">
                                <div className="bg-white/40 rounded-xl px-3 py-2 border border-black/[0.03]">
                                    <p className="text-gray-400 text-xs">Gateways</p>
                                    <p className="font-semibold text-gray-800">{gh.gateway_count}</p>
                                </div>
                                <div className="bg-white/40 rounded-xl px-3 py-2 border border-black/[0.03]">
                                    <p className="text-gray-400 text-xs">Sensoren</p>
                                    <p className="font-semibold text-gray-800">{gh.sensor_count}</p>
                                </div>
                            </div>
                        </Link>
                    ))}
                </div>
            )}

            {/* Delete Greenhouse Confirmation Modal */}
            {deletingGreenhouseId && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
                    <div className="absolute inset-0 bg-black/20 backdrop-blur-sm" onClick={() => setDeletingGreenhouseId(null)} />
                    <div className="relative bg-white/80 rounded-2xl border border-white/50 shadow-2xl backdrop-blur-xl p-6 max-w-sm w-full animate-in fade-in zoom-in duration-200">
                        <div className="w-12 h-12 rounded-full bg-red-50 text-red-500 flex items-center justify-center mb-4 mx-auto">
                            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                        </div>
                        <h3 className="text-lg font-semibold text-center text-gray-800 mb-2">Gewächshaus löschen?</h3>
                        <p className="text-sm text-center text-gray-500 mb-6">
                            Alle zugehörigen Gateways, Sensoren und Messdaten werden <strong className="font-semibold text-gray-700">unwiderruflich</strong> gelöscht.
                        </p>
                        <div className="flex gap-3">
                            <button
                                onClick={() => setDeletingGreenhouseId(null)}
                                className="flex-1 px-4 py-2.5 bg-white border border-gray-200 text-gray-700 rounded-xl text-sm font-medium hover:bg-gray-50 transition-colors"
                            >
                                Abbrechen
                            </button>
                            <button
                                onClick={confirmDeleteGreenhouse}
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
