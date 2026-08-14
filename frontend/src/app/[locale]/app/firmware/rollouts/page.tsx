'use client';

import { useState, useEffect, useCallback } from 'react';
import {
    listPolicies,
    createPolicy,
    deletePolicy,
    listReleases,
    RolloutPolicy,
    FirmwareRelease,
} from '@/lib/firmware-api';
import { apiListZones, Zone } from '@/lib/api';

export default function RolloutsPage() {
    const [policies, setPolicies] = useState<RolloutPolicy[]>([]);
    const [releases, setReleases] = useState<FirmwareRelease[]>([]);
    const [zones, setZones] = useState<Zone[]>([]);
    const [loading, setLoading] = useState(true);

    // Create modal
    const [showCreate, setShowCreate] = useState(false);
    const [selectedRelease, setSelectedRelease] = useState('');
    const [selectedZone, setSelectedZone] = useState('');
    const [canaryPct, setCanaryPct] = useState('100');
    const [creating, setCreating] = useState(false);

    // Delete confirm
    const [deletingId, setDeletingId] = useState<string | null>(null);
    const [deleting, setDeleting] = useState(false);

    const load = useCallback(async () => {
        try {
            const [p, r, z] = await Promise.all([
                listPolicies(),
                listReleases({ is_active: true }),
                apiListZones(),
            ]);
            setPolicies(p);
            setReleases(r.items);
            setZones(z);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const handleCreate = async () => {
        if (!selectedRelease) return;
        setCreating(true);
        try {
            await createPolicy({
                release_id: selectedRelease,
                zone_id: selectedZone || null,
                canary_percentage: canaryPct,
            });
            setShowCreate(false);
            setSelectedRelease('');
            setSelectedZone('');
            setCanaryPct('100');
            load();
        } catch (err) {
            console.error(err);
        } finally {
            setCreating(false);
        }
    };

    const handleDelete = async () => {
        if (!deletingId) return;
        setDeleting(true);
        try {
            await deletePolicy(deletingId);
            setDeletingId(null);
            load();
        } catch (err) {
            console.error(err);
        } finally {
            setDeleting(false);
        }
    };

    if (loading) {
        return <div className="animate-pulse"><div className="h-64 bg-black/[0.04] rounded-2xl" /></div>;
    }

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <p className="text-sm text-gray-500">{policies.length} Rollout-Policies definiert</p>
                <button
                    onClick={() => setShowCreate(true)}
                    disabled={releases.length === 0}
                    className="px-4 py-2 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-full text-sm font-medium hover:from-emerald-600 hover:to-emerald-700 transition-all shadow-sm disabled:opacity-50"
                >
                    + Policy erstellen
                </button>
            </div>

            {/* Policies Table */}
            <div className="glass-card overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="text-left text-xs text-gray-400 uppercase tracking-wider bg-black/[0.02]">
                                <th className="px-4 py-3">Release</th>
                                <th className="px-4 py-3">Zone</th>
                                <th className="px-4 py-3">Canary %</th>
                                <th className="px-4 py-3">Erstellt</th>
                                <th className="px-4 py-3 text-right">Aktion</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-black/[0.04]">
                            {policies.length === 0 ? (
                                <tr>
                                    <td colSpan={5} className="px-4 py-12 text-center text-gray-400">
                                        Keine Rollout-Policies. Erstelle eine, um Firmware gezielt auszurollen.
                                    </td>
                                </tr>
                            ) : (
                                policies.map((p) => (
                                    <tr key={p.id} className="hover:bg-black/[0.01] transition-colors">
                                        <td className="px-4 py-3 font-mono font-semibold text-gray-800">
                                            {p.release_version || p.release_id.slice(0, 8)}
                                        </td>
                                        <td className="px-4 py-3 text-gray-600">
                                            {p.zone_name || (
                                                <span className="text-gray-400 italic">Alle Zonen</span>
                                            )}
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                                                p.canary_percentage === '100'
                                                    ? 'bg-emerald-50 text-emerald-600'
                                                    : 'bg-amber-50 text-amber-600'
                                            }`}>
                                                {p.canary_percentage}%
                                            </span>
                                        </td>
                                        <td className="px-4 py-3 text-xs text-gray-400">
                                            {new Date(p.created_at).toLocaleString('de-CH')}
                                        </td>
                                        <td className="px-4 py-3 text-right">
                                            <button
                                                onClick={() => setDeletingId(p.id)}
                                                className="px-2.5 py-1 rounded-lg text-xs font-medium text-red-500 hover:bg-red-50 transition-colors"
                                            >
                                                Entfernen
                                            </button>
                                        </td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                    </table>
                </div>
            </div>

            {/* Create Modal */}
            {showCreate && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
                    <div className="glass-card p-8 w-full max-w-md mx-4 shadow-xl">
                        <h2 className="text-xl font-semibold text-gray-800 mb-6">Rollout Policy erstellen</h2>
                        <div className="space-y-4">
                            <div>
                                <label className="block text-xs font-medium text-gray-500 mb-1">Release *</label>
                                <select value={selectedRelease} onChange={(e) => setSelectedRelease(e.target.value)} className="w-full px-3 py-2 rounded-xl bg-white/60 border border-black/[0.06] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30">
                                    <option value="">Release wählen…</option>
                                    {releases.map((r) => (
                                        <option key={r.id} value={r.id}>{r.version} ({r.board_type})</option>
                                    ))}
                                </select>
                            </div>
                            <div>
                                <label className="block text-xs font-medium text-gray-500 mb-1">Zone (optional)</label>
                                <select value={selectedZone} onChange={(e) => setSelectedZone(e.target.value)} className="w-full px-3 py-2 rounded-xl bg-white/60 border border-black/[0.06] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30">
                                    <option value="">Alle Zonen</option>
                                    {zones.map((z) => (
                                        <option key={z.id} value={z.id}>{z.name}</option>
                                    ))}
                                </select>
                            </div>
                            <div>
                                <label className="block text-xs font-medium text-gray-500 mb-1">Canary Percentage</label>
                                <input type="number" min="1" max="100" value={canaryPct} onChange={(e) => setCanaryPct(e.target.value)} className="w-full px-3 py-2 rounded-xl bg-white/60 border border-black/[0.06] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30" />
                            </div>
                            <div className="flex gap-3 pt-2">
                                <button onClick={() => setShowCreate(false)} className="flex-1 py-2.5 bg-black/[0.04] text-gray-600 rounded-xl text-sm font-medium hover:bg-black/[0.06] transition-colors">Abbrechen</button>
                                <button onClick={handleCreate} disabled={!selectedRelease || creating} className="flex-1 py-2.5 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-xl text-sm font-medium hover:from-emerald-600 hover:to-emerald-700 transition-all disabled:opacity-50 shadow-sm">
                                    {creating ? 'Erstelle…' : 'Erstellen'}
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* Delete confirm */}
            {deletingId && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
                    <div className="absolute inset-0 bg-black/20 backdrop-blur-sm" onClick={() => setDeletingId(null)} />
                    <div className="relative bg-white/80 rounded-2xl border border-white/50 shadow-2xl backdrop-blur-xl p-6 max-w-sm w-full">
                        <h3 className="text-lg font-semibold text-center text-gray-800 mb-2">Policy entfernen?</h3>
                        <p className="text-sm text-center text-gray-500 mb-6">Die Rollout-Einschränkung wird sofort aufgehoben.</p>
                        <div className="flex gap-3">
                            <button onClick={() => setDeletingId(null)} className="flex-1 px-4 py-2.5 bg-white border border-gray-200 text-gray-700 rounded-xl text-sm font-medium hover:bg-gray-50 transition-colors">Abbrechen</button>
                            <button onClick={handleDelete} disabled={deleting} className="flex-1 px-4 py-2.5 bg-red-500 text-white rounded-xl text-sm font-medium hover:bg-red-600 transition-colors disabled:opacity-50">
                                {deleting ? 'Lösche…' : 'Entfernen'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
