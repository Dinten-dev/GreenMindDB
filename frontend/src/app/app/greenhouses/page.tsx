'use client';

import { useState, useEffect } from 'react';
import { apiListGreenhouses, apiCreateGreenhouse, apiListGateways, apiListSensors, Greenhouse, GatewayInfo, SensorInfo } from '@/lib/api';

export default function GreenhousesPage() {
    const [greenhouses, setGreenhouses] = useState<Greenhouse[]>([]);
    const [loading, setLoading] = useState(true);
    const [showCreate, setShowCreate] = useState(false);
    const [name, setName] = useState('');
    const [location, setLocation] = useState('');
    const [creating, setCreating] = useState(false);

    // Detail view state
    const [selectedGh, setSelectedGh] = useState<string | null>(null);
    const [ghGateways, setGhGateways] = useState<GatewayInfo[]>([]);
    const [ghSensors, setGhSensors] = useState<SensorInfo[]>([]);
    const [loadingDetail, setLoadingDetail] = useState(false);

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

    const handleSelectGreenhouse = async (ghId: string) => {
        if (selectedGh === ghId) {
            setSelectedGh(null);
            return;
        }
        setSelectedGh(ghId);
        setLoadingDetail(true);
        try {
            const [gateways, sensors] = await Promise.all([
                apiListGateways(ghId),
                apiListSensors(ghId),
            ]);
            setGhGateways(gateways);
            setGhSensors(sensors);
        } catch (err) {
            console.error(err);
        } finally {
            setLoadingDetail(false);
        }
    };

    // Group sensors by gateway_id
    const sensorsByGateway = ghSensors.reduce<Record<string, SensorInfo[]>>((acc, s) => {
        const key = s.gateway_id;
        if (!acc[key]) acc[key] = [];
        acc[key].push(s);
        return acc;
    }, {});

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
                                    placeholder="z.B. Basel, Schweiz"
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
                    {greenhouses.map(gh => {
                        const isSelected = selectedGh === gh.id;
                        return (
                            <div key={gh.id}>
                                {/* Greenhouse Card */}
                                <button
                                    onClick={() => handleSelectGreenhouse(gh.id)}
                                    className={`w-full text-left glass-card p-6 transition-all duration-300 ${
                                        isSelected ? 'border-emerald-500/20 shadow-lg' : ''
                                    }`}
                                >
                                    <div className="flex items-start justify-between">
                                        <div>
                                            <h3 className="text-lg font-semibold text-gray-800 mb-1">{gh.name}</h3>
                                            {gh.location && <p className="text-sm text-gray-400">{gh.location}</p>}
                                        </div>
                                        <span className={`text-gray-400 transition-transform duration-200 text-lg ${isSelected ? 'rotate-90' : ''}`}>
                                            ›
                                        </span>
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
                                </button>

                                {/* Detail Panel */}
                                {isSelected && (
                                    <div className="mt-3 ml-4 border-l-2 border-emerald-200 pl-5 space-y-3 pb-2">
                                        {loadingDetail ? (
                                            <div className="animate-pulse space-y-3">
                                                <div className="h-20 bg-black/[0.03] rounded-2xl" />
                                                <div className="h-20 bg-black/[0.03] rounded-2xl" />
                                            </div>
                                        ) : ghGateways.length === 0 ? (
                                            <div className="bg-white/30 rounded-2xl p-6 text-center backdrop-blur-sm border border-black/[0.04]">
                                                <p className="text-sm text-gray-400">
                                                    Keine Gateways zugeordnet. Gehe zu <span className="font-medium text-gray-600">Gateways</span>, um ein Gateway zu pairen.
                                                </p>
                                            </div>
                                        ) : (
                                            ghGateways.map(gw => {
                                                const gwSensors = sensorsByGateway[gw.id] || [];
                                                return (
                                                    <div key={gw.id} className="glass-card overflow-hidden">
                                                        {/* Gateway Header */}
                                                        <div className="px-5 py-4 flex items-center justify-between border-b border-black/[0.04]">
                                                            <div className="flex items-center gap-3">
                                                                <div className="w-8 h-8 rounded-xl bg-blue-50 flex items-center justify-center text-sm">
                                                                    🖥
                                                                </div>
                                                                <div>
                                                                    <p className="font-semibold text-gray-800 text-sm">{gw.name || gw.hardware_id}</p>
                                                                    <p className="text-xs text-gray-400 font-mono">{gw.hardware_id}</p>
                                                                </div>
                                                            </div>
                                                            <div className="flex items-center gap-3">
                                                                {gw.local_ip && (
                                                                    <span className="text-xs text-gray-400 bg-black/[0.03] px-2 py-0.5 rounded-lg font-mono">
                                                                        {gw.local_ip}
                                                                    </span>
                                                                )}
                                                                <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
                                                                    gw.status === 'online'
                                                                        ? 'bg-emerald-50 text-emerald-600'
                                                                        : 'bg-gray-100 text-gray-400'
                                                                }`}>
                                                                    <span className={`w-1.5 h-1.5 rounded-full ${gw.status === 'online' ? 'bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.4)]' : 'bg-gray-300'}`} />
                                                                    {gw.status}
                                                                </span>
                                                            </div>
                                                        </div>

                                                        {/* Sensor List */}
                                                        {gwSensors.length > 0 ? (
                                                            <div className="divide-y divide-black/[0.03]">
                                                                {gwSensors.map(sensor => (
                                                                    <div key={sensor.id} className="px-5 py-3 flex items-center justify-between">
                                                                        <div className="flex items-center gap-2.5">
                                                                            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                                                                            <span className="text-sm text-gray-700">
                                                                                {sensor.name || sensor.mac_address}
                                                                            </span>
                                                                        </div>
                                                                        <div className="flex items-center gap-3 text-xs text-gray-400">
                                                                            <span className="bg-black/[0.03] px-2 py-0.5 rounded-lg">{sensor.sensor_type}</span>
                                                                            <span className="font-mono">{sensor.mac_address}</span>
                                                                            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full ${
                                                                                sensor.status === 'online'
                                                                                    ? 'bg-emerald-50 text-emerald-600'
                                                                                    : 'bg-gray-100 text-gray-400'
                                                                            }`}>
                                                                                <span className={`w-1 h-1 rounded-full ${sensor.status === 'online' ? 'bg-emerald-500' : 'bg-gray-300'}`} />
                                                                                {sensor.status}
                                                                            </span>
                                                                        </div>
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        ) : (
                                                            <div className="px-5 py-3 text-xs text-gray-400">
                                                                Keine Sensoren – das Gateway entdeckt ESP32-Module automatisch via mDNS
                                                            </div>
                                                        )}
                                                    </div>
                                                );
                                            })
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
