'use client';

import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import {
    apiListGreenhouses, apiListGateways, apiListSensors,
    Greenhouse, GatewayInfo, SensorInfo,
} from '@/lib/api';

export default function DashboardPage() {
    const { user, createOrg, refresh } = useAuth();
    const [greenhouses, setGreenhouses] = useState<Greenhouse[]>([]);
    const [gateways, setGateways] = useState<GatewayInfo[]>([]);
    const [sensors, setSensors] = useState<SensorInfo[]>([]);
    const [loading, setLoading] = useState(true);
    const [orgName, setOrgName] = useState('');
    const [creatingOrg, setCreatingOrg] = useState(false);

    const loadData = useCallback(async () => {
        try {
            const [gh, gw, sen] = await Promise.all([
                apiListGreenhouses(),
                apiListGateways(),
                apiListSensors(),
            ]);
            setGreenhouses(gh);
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

    // ── No Organization ──
    if (!loading && !user?.organization_id) {
        return (
            <div className="max-w-md mx-auto mt-24">
                <div className="glass-card p-8 text-center">
                    <div className="text-4xl mb-4">🏢</div>
                    <h2 className="text-xl font-semibold text-gray-800 mb-2">Organisation erstellen</h2>
                    <p className="text-sm text-gray-400 mb-6">
                        Eine Organisation gruppiert Gewächshäuser, Gateways und Teammitglieder.
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
                <StatCard label="Gewächshäuser" value={greenhouses.length} icon="⌂" />
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
                            <div key={gw.id} className="flex items-center gap-3 px-4 py-3 bg-white/40 rounded-xl border border-black/[0.03] transition-colors hover:bg-white/60">
                                <span className={`w-2.5 h-2.5 rounded-full ${gw.status === 'online' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.4)]' : 'bg-gray-300'}`} />
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium text-gray-800 truncate">{gw.name || gw.hardware_id}</p>
                                    <p className="text-xs text-gray-400">{gw.sensor_count} Sensoren · {gw.greenhouse_name}</p>
                                </div>
                                <span className="text-xs text-gray-400">
                                    {gw.last_seen ? timeAgo(gw.last_seen) : 'nie'}
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
            ) : (
                <div className="glass-card p-12 text-center">
                    <div className="text-4xl mb-4">🖥</div>
                    <h3 className="text-lg font-semibold text-gray-800 mb-2">Noch keine Gateways</h3>
                    <p className="text-sm text-gray-400">
                        Erstelle ein Gewächshaus und paire ein Raspberry Pi Gateway, um Sensordaten zu empfangen.
                    </p>
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
