'use client';

import { useState, useEffect } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { apiListGateways, apiListSensors, apiListGreenhouses, GatewayInfo, SensorInfo, Greenhouse } from '@/lib/api';

export default function GreenhouseDetailPage() {
    const { id } = useParams<{ id: string }>();
    const [greenhouse, setGreenhouse] = useState<Greenhouse | null>(null);
    const [gateways, setGateways] = useState<GatewayInfo[]>([]);
    const [sensors, setSensors] = useState<SensorInfo[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        (async () => {
            try {
                const [ghs, gws, sens] = await Promise.all([
                    apiListGreenhouses(),
                    apiListGateways(id),
                    apiListSensors(id),
                ]);
                setGreenhouse(ghs.find(g => g.id === id) || null);
                setGateways(gws);
                setSensors(sens);
            } catch (err) {
                console.error(err);
            } finally {
                setLoading(false);
            }
        })();
    }, [id]);

    const sensorsByGateway = sensors.reduce<Record<string, SensorInfo[]>>((acc, s) => {
        const key = s.gateway_id;
        if (!acc[key]) acc[key] = [];
        acc[key].push(s);
        return acc;
    }, {});

    if (loading) {
        return (
            <div className="animate-pulse space-y-4">
                <div className="h-8 w-48 bg-black/[0.04] rounded-xl" />
                <div className="h-40 bg-black/[0.04] rounded-2xl" />
            </div>
        );
    }

    return (
        <div className="space-y-6">
            {/* Breadcrumb */}
            <div className="flex items-center gap-2 text-sm text-gray-400">
                <Link href="/app/greenhouses" className="hover:text-gray-600 transition-colors">Gewächshäuser</Link>
                <span>›</span>
                <span className="text-gray-700 font-medium">{greenhouse?.name || 'Detail'}</span>
            </div>

            <div>
                <h1 className="text-2xl font-bold text-gray-800 tracking-tight">{greenhouse?.name}</h1>
                {greenhouse?.location && <p className="text-sm text-gray-400 mt-1">{greenhouse.location}</p>}
            </div>

            {/* Stats */}
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <div className="glass-card p-5">
                    <p className="text-[11px] text-gray-400 uppercase tracking-wider font-medium mb-2">Gateways</p>
                    <p className="text-2xl font-bold text-gray-800">{gateways.length}</p>
                </div>
                <div className="glass-card p-5">
                    <p className="text-[11px] text-gray-400 uppercase tracking-wider font-medium mb-2">Sensoren</p>
                    <p className="text-2xl font-bold text-gray-800">{sensors.length}</p>
                </div>
                <div className="glass-card p-5">
                    <p className="text-[11px] text-gray-400 uppercase tracking-wider font-medium mb-2">Online</p>
                    <p className="text-2xl font-bold text-emerald-600">{gateways.filter(g => g.status === 'online').length}</p>
                </div>
            </div>

            {/* Gateway + Sensor Tree */}
            {gateways.length === 0 ? (
                <div className="glass-card p-12 text-center">
                    <div className="text-4xl mb-4">🖥</div>
                    <h3 className="text-lg font-semibold text-gray-800 mb-2">Keine Gateways</h3>
                    <p className="text-sm text-gray-400">Gehe zu <Link href="/app/gateways" className="text-emerald-600 hover:underline">Gateways</Link>, um eines zu pairen.</p>
                </div>
            ) : (
                <div className="space-y-4">
                    {gateways.map(gw => {
                        const gwSensors = sensorsByGateway[gw.id] || [];
                        return (
                            <div key={gw.id} className="glass-card overflow-hidden">
                                {/* Gateway Header */}
                                <div className="px-5 py-4 flex items-center justify-between border-b border-black/[0.04]">
                                    <div className="flex items-center gap-3">
                                        <div className="w-8 h-8 rounded-xl bg-blue-50 flex items-center justify-center text-sm">🖥</div>
                                        <div>
                                            <p className="font-semibold text-gray-800 text-sm">{gw.name || gw.hardware_id}</p>
                                            <p className="text-xs text-gray-400 font-mono">{gw.hardware_id}</p>
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-3">
                                        {gw.local_ip && (
                                            <span className="text-xs text-gray-400 bg-black/[0.03] px-2 py-0.5 rounded-lg font-mono">{gw.local_ip}</span>
                                        )}
                                        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
                                            gw.status === 'online' ? 'bg-emerald-50 text-emerald-600' : 'bg-gray-100 text-gray-400'
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
                                            <Link
                                                key={sensor.id}
                                                href={`/app/greenhouses/${id}/sensor/${sensor.id}`}
                                                className="px-5 py-3 flex items-center justify-between hover:bg-white/40 transition-colors group"
                                            >
                                                <div className="flex items-center gap-2.5">
                                                    <span className={`w-1.5 h-1.5 rounded-full ${sensor.status === 'online' ? 'bg-emerald-400' : 'bg-gray-300'}`} />
                                                    <span className="text-sm text-gray-700 group-hover:text-emerald-700 transition-colors">
                                                        {sensor.name || sensor.mac_address}
                                                    </span>
                                                </div>
                                                <div className="flex items-center gap-3 text-xs text-gray-400">
                                                    <span className="bg-black/[0.03] px-2 py-0.5 rounded-lg">{sensor.sensor_type}</span>
                                                    <span className="font-mono">{sensor.mac_address}</span>
                                                    <span className="text-gray-300 group-hover:text-emerald-500 transition-colors">→</span>
                                                </div>
                                            </Link>
                                        ))}
                                    </div>
                                ) : (
                                    <div className="px-5 py-3 text-xs text-gray-400">
                                        Keine Sensoren – ESP32-Module werden automatisch via mDNS erkannt
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
