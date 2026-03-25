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
                <div className="bg-white rounded-apple-lg shadow-apple p-8 text-center">
                    <div className="text-4xl mb-4">🏢</div>
                    <h2 className="text-xl font-semibold text-apple-gray-800 mb-2">Organisation erstellen</h2>
                    <p className="text-sm text-apple-gray-400 mb-6">
                        Eine Organisation gruppiert Gewächshäuser, Gateways und Teammitglieder.
                    </p>
                    <input
                        type="text"
                        value={orgName}
                        onChange={(e) => setOrgName(e.target.value)}
                        className="w-full px-4 py-2.5 rounded-apple bg-apple-gray-100 border border-apple-gray-200 text-sm text-apple-gray-800 placeholder-apple-gray-400 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:border-transparent mb-4"
                        placeholder="Name der Organisation"
                    />
                    <button
                        onClick={handleCreateOrg}
                        disabled={creatingOrg || !orgName.trim()}
                        className="w-full py-2.5 bg-gm-green-500 text-white rounded-apple font-medium text-sm hover:bg-gm-green-600 transition-colors disabled:opacity-50"
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
                <div className="h-8 w-48 bg-apple-gray-200 rounded-lg" />
                <div className="grid grid-cols-4 gap-4">
                    {[1, 2, 3, 4].map(i => <div key={i} className="h-24 bg-apple-gray-200 rounded-apple-lg" />)}
                </div>
                <div className="h-80 bg-apple-gray-200 rounded-apple-lg" />
            </div>
        );
    }

    const onlineGateways = gateways.filter(g => g.status === 'online').length;

    return (
        <div className="space-y-8">
            {/* Header */}
            <div>
                <h1 className="text-2xl font-bold text-apple-gray-800">Dashboard</h1>
                <p className="text-sm text-apple-gray-400 mt-1">
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
                <div className="bg-white rounded-apple-lg shadow-apple-card p-6">
                    <h2 className="text-lg font-semibold text-apple-gray-800 mb-4">Gateways</h2>
                    <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-3">
                        {gateways.map(gw => (
                            <div key={gw.id} className="flex items-center gap-3 px-4 py-3 bg-apple-gray-100/50 rounded-apple">
                                <span className={`w-2 h-2 rounded-full ${gw.status === 'online' ? 'bg-gm-green-500' : 'bg-apple-gray-300'}`} />
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium text-apple-gray-800 truncate">{gw.name || gw.hardware_id}</p>
                                    <p className="text-xs text-apple-gray-400">{gw.sensor_count} Sensoren · {gw.greenhouse_name}</p>
                                </div>
                                <span className="text-xs text-apple-gray-400">
                                    {gw.last_seen ? timeAgo(gw.last_seen) : 'nie'}
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
            ) : (
                <div className="bg-white rounded-apple-lg shadow-apple-card p-12 text-center">
                    <div className="text-4xl mb-4">🖥</div>
                    <h3 className="text-lg font-semibold text-apple-gray-800 mb-2">Noch keine Gateways</h3>
                    <p className="text-sm text-apple-gray-400">
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
        <div className={`rounded-apple-lg p-5 ${accent ? 'bg-gm-green-50 border border-gm-green-200' : 'bg-white shadow-apple-card'}`}>
            <div className="flex items-center gap-2 mb-2">
                <span className="text-base text-apple-gray-400">{icon}</span>
                <span className="text-xs text-apple-gray-400 uppercase tracking-wide">{label}</span>
            </div>
            <p className={`text-2xl font-bold ${accent ? 'text-gm-green-600' : 'text-apple-gray-800'}`}>
                {value.toLocaleString()}
            </p>
            {sub && <p className="text-xs text-apple-gray-400 mt-0.5">{sub}</p>}
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
