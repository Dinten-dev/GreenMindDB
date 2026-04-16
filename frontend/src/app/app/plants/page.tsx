'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { apiListPlants, apiCreatePlant } from '@/lib/plants-api';
import { apiListZones } from '@/lib/api';
import { Plant, Zone } from '@/types';

export default function PlantsPage() {
    const [plants, setPlants] = useState<Plant[]>([]);
    const [zones, setZones] = useState<Zone[]>([]);
    const [loading, setLoading] = useState(true);
    
    // Create Form
    const [showCreate, setShowCreate] = useState(false);
    const [creating, setCreating] = useState(false);
    const [name, setName] = useState('');
    const [plantCode, setPlantCode] = useState('');
    const [zoneId, setZoneId] = useState('');

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const [pData, zData] = await Promise.all([
                apiListPlants(),
                apiListZones()
            ]);
            setPlants(pData);
            setZones(zData);
            if (zData.length > 0 && !zoneId) {
                setZoneId(zData[0].id);
            }
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const handleCreate = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!name.trim() || !zoneId) return;
        setCreating(true);
        try {
            await apiCreatePlant({
                name,
                plant_code: plantCode,
                zone_id: zoneId,
            });
            setName('');
            setPlantCode('');
            setShowCreate(false);
            await loadData();
        } catch (err) {
            console.error(err);
        } finally {
            setCreating(false);
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
                    <h1 className="text-2xl font-bold text-gray-800 tracking-tight">Pflanzen</h1>
                    <p className="text-sm text-gray-400 mt-1">Verwalte deine Pflanzen und Sensoren</p>
                </div>
                <button
                    onClick={() => setShowCreate(true)}
                    className="px-4 py-2 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-full text-sm font-medium hover:from-emerald-600 hover:to-emerald-700 transition-all duration-200 shadow-sm whitespace-nowrap"
                >
                    + Neue Pflanze
                </button>
            </div>

            {/* Create Modal */}
            {showCreate && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
                    <div className="glass-card p-8 w-full max-w-md mx-4 shadow-xl">
                        <h2 className="text-xl font-semibold text-gray-800 mb-6">Pflanze erfassen</h2>
                        {zones.length === 0 ? (
                            <div className="text-red-500 text-sm mb-4">Bitte erstelle zuerst eine Zone.</div>
                        ) : (
                            <form onSubmit={handleCreate} className="space-y-4">
                                <div>
                                    <label className="block text-sm font-medium text-gray-600 mb-1.5">Zone</label>
                                    <select
                                        value={zoneId}
                                        onChange={(e) => setZoneId(e.target.value)}
                                        className="w-full px-4 py-2.5 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                                    >
                                        {zones.map(z => (
                                            <option key={z.id} value={z.id}>{z.name}</option>
                                        ))}
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-gray-600 mb-1.5">Name</label>
                                    <input
                                        type="text"
                                        required
                                        value={name}
                                        onChange={(e) => setName(e.target.value)}
                                        className="w-full px-4 py-2.5 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                                        placeholder="z.B. Tomate 01"
                                    />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-gray-600 mb-1.5">Interne ID (Optional)</label>
                                    <input
                                        type="text"
                                        value={plantCode}
                                        onChange={(e) => setPlantCode(e.target.value)}
                                        className="w-full px-4 py-2.5 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                                        placeholder="z.B. TOM-2026-A1"
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
                                        {creating ? 'Speichere…' : 'Anlegen'}
                                    </button>
                                </div>
                            </form>
                        )}
                    </div>
                </div>
            )}

            {/* List */}
            {plants.length === 0 ? (
                <div className="glass-card p-12 text-center">
                    <div className="text-4xl mb-4">🌱</div>
                    <h3 className="text-lg font-semibold text-gray-800 mb-2">Noch keine Pflanzen</h3>
                    <p className="text-sm text-gray-400 mb-4">
                        Lege Pflanzen an, um die Beobachtungs-App nutzen zu können.
                    </p>
                </div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                    {plants.map(p => {
                        const zoneName = zones.find(z => z.id === p.zone_id)?.name || 'Unbekannt';
                        return (
                            <Link key={p.id} href={`/app/plants/${p.id}`} className="block glass-card p-5 hover:shadow-lg transition">
                                <div className="flex items-start justify-between">
                                    <div>
                                        <h3 className="font-semibold text-gray-800">{p.name}</h3>
                                        <p className="text-xs text-gray-400 mt-0.5">{p.plant_code || 'Ohne Code'}</p>
                                    </div>
                                    {p.current_sensor_id && (
                                        <span className="inline-flex px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-50 text-emerald-600">
                                            Sensor aktiv
                                        </span>
                                    )}
                                </div>
                                <div className="mt-4 pt-4 border-t border-black/[0.04] flex items-center justify-between text-sm">
                                    <span className="text-gray-500">Zone: {zoneName}</span>
                                    <span className="text-gray-400">→</span>
                                </div>
                            </Link>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
