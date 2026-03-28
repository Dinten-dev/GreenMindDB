'use client';

import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import {
    apiListZones, apiListGateways, apiListSensors, apiDeleteGateway,
    Zone, GatewayInfo, SensorInfo,
} from '@/lib/api';

export default function DashboardPage() {
    const { user, createOrg, refresh } = useAuth();
    const [zones, setZones] = useState<Zone[]>([]);
    const [gateways, setGateways] = useState<GatewayInfo[]>([]);
    const [sensors, setSensors] = useState<SensorInfo[]>([]);
    const [loading, setLoading] = useState(true);
    const [orgName, setOrgName] = useState('');
    const [creatingOrg, setCreatingOrg] = useState(false);
    
    // Deletion states
    const [deletingGatewayId, setDeletingGatewayId] = useState<string | null>(null);
    const [isDeleting, setIsDeleting] = useState(false);

    const loadData = useCallback(async () => {
        try {
            const [z, gw, sen] = await Promise.all([
                apiListZones(),
                apiListGateways(),
                apiListSensors(),
            ]);
            setZones(z);
            setGateways(gw);
            setSensors(sen);
        } catch (err) {
            console.error('Dashboard load error:', err);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        if (user?.organization_id) loadData();
        else setLoading(false);
    }, [user?.organization_id, loadData]);

    const handleCreateOrg = async () => {
        if (!orgName.trim()) return;
        setCreatingOrg(true);
        try {
            await createOrg(orgName);
            await refresh();
        } catch (err) {
            console.error(err);
        } finally {
            setCreatingOrg(false);
        }
    };

    const confirmDelete = async () => {
        if (!deletingGatewayId) return;
        setIsDeleting(true);
        try {
            await apiDeleteGateway(deletingGatewayId);
            setGateways(prev => prev.filter(g => g.id !== deletingGatewayId));
            // Reload sensors to reflect cascade delete
            const sen = await apiListSensors();
            setSensors(sen);
            setDeletingGatewayId(null);
        } catch (err) {
            console.error('Failed to delete gateway:', err);
        } finally {
            setIsDeleting(false);
        }
    };

    // ── No Organization ──
    if (!loading && !user?.organization_id) {
        return (
            <div className="max-w-md mx-auto mt-24">
                <div className="glass-card p-8 text-center">
                    <div className="text-4xl mb-4">🏢</div>
                    <h2 className="text-xl font-semibold text-gray-800 mb-2">Organisation erstellen</h2>
                    <p className="text-sm text-gray-400 mb-6">
                        Eine Organisation gruppiert Zonen, Gateways und Teammitglieder.
                    </p>
                    <input
                        type="text"
                        value={orgName}
                        onChange={(e) => setOrgName(e.target.value)}
                        className="w-full px-4 py-2.5 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-500/30 backdrop-blur-sm mb-4"
                        placeholder="Name der Organisation"
                    />
                    <button
                        onClick={handleCreateOrg}
                        disabled={creatingOrg || !orgName.trim()}
                        className="w-full py-2.5 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-xl font-medium text-sm hover:from-emerald-600 hover:to-emerald-700 transition-all duration-200 disabled:opacity-50 shadow-sm"
                    >
                        {creatingOrg ? 'Erstelle…' : 'Organisation erstellen'}
                    </button>
                </div>
            </div>
        );
    }

    if (loading) {
        return (
            <div className="animate-pulse space-y-6">
                <div className="h-8 w-48 bg-black/[0.04] rounded-xl" />
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    {[1, 2, 3, 4].map(i => <div key={i} className="h-28 bg-black/[0.04] rounded-2xl" />)}
                </div>
                <div className="h-80 bg-black/[0.04] rounded-2xl" />
            </div>
        );
    }

    const onlineGateways = gateways.filter(g => g.status === 'online').length;

    return (
        <div className="space-y-8">
            {/* Header */}
            <div>
                <h1 className="text-2xl font-bold text-gray-800 tracking-tight">Dashboard</h1>
                <p className="text-sm text-gray-400 mt-1">
                    {user?.organization_name || 'Deine Organisation'} – Überblick
                </p>
            </div>

            {/* Stat Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <StatCard label="Zonen" value={zones.length} icon="⌂" />
                <StatCard label="Gateways" value={gateways.length} sub={`${onlineGateways} online`} icon="◎" />
                <StatCard label="Sensoren" value={sensors.length} icon="📡" />
                <StatCard label="Online" value={onlineGateways} sub={`von ${gateways.length}`} icon="◉" accent />
            </div>

            {/* Gateway Status */}
            {gateways.length > 0 ? (
                <div className="glass-card p-6">
                    <h2 className="text-lg font-semibold text-gray-800 mb-4">Gateways</h2>
                    <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-3">
                        {gateways.map(gw => (
                            <div key={gw.id} className="group flex items-center gap-3 px-4 py-3 bg-white/40 rounded-xl border border-black/[0.03] transition-colors hover:bg-white/60 relative">
                                <span className={`w-2.5 h-2.5 rounded-full ${gw.status === 'online' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.4)]' : 'bg-gray-300'}`} />
                                <div className="flex-1 min-w-0 pr-8">
                                    <p className="text-sm font-medium text-gray-800 truncate">{gw.name || gw.hardware_id}</p>
                                    <p className="text-xs text-gray-400">{gw.sensor_count} Sensoren · {gw.zone_name}</p>
                                </div>
                                <span className="text-xs text-gray-400 shrink-0">
                                    {gw.last_seen ? timeAgo(gw.last_seen) : 'nie'}
                                </span>
                                <button
                                    onClick={() => setDeletingGatewayId(gw.id)}
                                    className="absolute right-2.5 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all"
                                    title="Gateway entfernen"
                                >
                                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                    </svg>
                                </button>
                            </div>
                        ))}
                    </div>
                </div>
            ) : (
                <div className="glass-card p-12 text-center">
                    <div className="text-4xl mb-4">🖥</div>
                    <h3 className="text-lg font-semibold text-gray-800 mb-2">Noch keine Gateways</h3>
                    <p className="text-sm text-gray-400">
                        Erstelle eine Zone und paire ein Raspberry Pi Gateway, um Sensordaten zu empfangen.
                    </p>
                </div>
            )}

            {/* Delete Confirmation Modal */}
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
                            Bist du sicher, dass du dieses Gateway löschen möchtest? Alle zugehörigen Sensoren und Daten werden ebenfalls entfernt. Dies kann nicht rückgängig gemacht werden.
                        </p>
                        <div className="flex gap-3">
                            <button
                                onClick={() => setDeletingGatewayId(null)}
                                className="flex-1 px-4 py-2.5 bg-white border border-gray-200 text-gray-700 rounded-xl text-sm font-medium hover:bg-gray-50 transition-colors"
                            >
                                Abbrechen
                            </button>
                            <button
                                onClick={confirmDelete}
                                disabled={isDeleting}
                                className="flex-1 px-4 py-2.5 bg-red-500 text-white rounded-xl text-sm font-medium hover:bg-red-600 transition-colors disabled:opacity-50"
                            >
                                {isDeleting ? 'Lösche...' : 'Löschen'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}


function StatCard({ label, value, sub, icon, accent }: {
    label: string; value: number; sub?: string; icon: string; accent?: boolean;
}) {
    return (
        <div className={`glass-card accent-glow p-5 ${accent ? 'border-emerald-500/20' : ''}`}>
            <div className="flex items-center gap-2 mb-3">
                <span className="text-base text-gray-400">{icon}</span>
                <span className="text-[11px] text-gray-400 uppercase tracking-wider font-medium">{label}</span>
            </div>
            <p className={`text-2xl font-bold tracking-tight ${accent ? 'text-emerald-600' : 'text-gray-800'}`}>
                {value.toLocaleString()}
            </p>
            {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
        </div>
    );
}

function timeAgo(iso: string): string {
    const diff = Date.now() - new Date(iso).getTime();
    const sec = Math.floor(diff / 1000);
    if (sec < 60) return `vor ${sec}s`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `vor ${min}m`;
    const hrs = Math.floor(min / 60);
    if (hrs < 24) return `vor ${hrs}h`;
    return `vor ${Math.floor(hrs / 24)}d`;
}
