'use client';

import { useEffect, useState } from 'react';
import { fetchGreenhouse, fetchDevices, Greenhouse, Device } from '@/lib/api';
import Link from 'next/link';

export default function OperatorGreenhouseDetailClient({ id }: { id: string }) {
    const [greenhouse, setGreenhouse] = useState<Greenhouse | null>(null);
    const [devices, setDevices] = useState<Device[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        Promise.all([
            fetchGreenhouse(id),
            fetchDevices()
        ])
            .then(([gh, devs]) => {
                setGreenhouse(gh);
                setDevices(devs);
            })
            .catch(err => setError(err.message))
            .finally(() => setLoading(false));
    }, [id]);

    if (loading) return <div className="p-8 text-center text-gray-500">Loading greenhouse details...</div>;
    if (error) return <div className="p-8 text-center text-red-600">Error: {error}</div>;
    if (!greenhouse) return <div className="p-8 text-center text-gray-500">Greenhouse not found</div>;

    return (
        <main className="max-w-7xl mx-auto px-6 py-8">
            <div className="flex items-center gap-4 mb-8">
                <Link href="/operator/greenhouses" className="text-sm text-gray-500 hover:text-green-600 transition-colors">
                    ← Back to Greenhouses
                </Link>
            </div>

            <div className="bg-white rounded-2xl shadow-sm border border-gray-200 p-8 mb-8">
                <div className="flex justify-between items-start">
                    <div>
                        <h1 className="text-3xl font-bold text-gray-900 mb-2">{greenhouse.name}</h1>
                        <p className="text-gray-500">{greenhouse.location || 'No location'}</p>
                    </div>
                    <div className="flex items-center gap-2 bg-gray-100 px-3 py-1.5 rounded-full text-sm text-gray-700">
                        <span className="text-lg">🕒</span>
                        <span>{greenhouse.timezone}</span>
                    </div>
                </div>
            </div>

            <h2 className="text-2xl font-bold text-gray-900 mb-6">Devices</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {devices.map(device => (
                    <Link
                        key={device.id}
                        href={`/operator/devices/${device.id}`}
                        className="block bg-white rounded-xl shadow-sm border border-gray-200 hover:shadow-md hover:border-green-500 transition-all p-6 group"
                    >
                        <div className="flex justify-between items-start mb-4">
                            <div>
                                <h3 className="text-lg font-semibold text-gray-900 group-hover:text-green-700 transition-colors">
                                    {device.serial}
                                </h3>
                                <p className="text-sm text-gray-500 capitalize">{device.type.replace('_', ' ')}</p>
                            </div>
                            <span className={`w-3 h-3 rounded-full ${device.status === 'online' ? 'bg-green-500 animate-pulse' : 'bg-gray-300'}`} />
                        </div>

                        <div className="space-y-2 mt-4 text-sm text-gray-600">
                            <div className="flex justify-between">
                                <span>Firmware:</span>
                                <span className="font-mono">{device.fw_version || 'N/A'}</span>
                            </div>
                            <div className="flex justify-between">
                                <span>Last Seen:</span>
                                <span>{device.last_seen ? new Date(device.last_seen).toLocaleString() : 'Never'}</span>
                            </div>
                        </div>
                    </Link>
                ))}
            </div>

            {devices.length === 0 && (
                <div className="text-center py-12 bg-gray-50 rounded-xl border border-dashed border-gray-300">
                    <p className="text-gray-500">No devices assigned to this greenhouse.</p>
                </div>
            )}
        </main>
    );
}
