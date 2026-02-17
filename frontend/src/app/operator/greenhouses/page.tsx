'use client';

import { useEffect, useState } from 'react';
import { fetchGreenhouses, Greenhouse } from '@/lib/api';
import Link from 'next/link';

export default function OperatorGreenhousesPage() {
    const [greenhouses, setGreenhouses] = useState<Greenhouse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        fetchGreenhouses()
            .then(setGreenhouses)
            .catch(err => setError(err.message))
            .finally(() => setLoading(false));
    }, []);

    if (loading) return <div className="p-8 text-center">Loading greenhouses...</div>;
    if (error) return <div className="p-8 text-center text-red-600">Error: {error}</div>;

    return (
        <main className="max-w-7xl mx-auto px-6 py-8">
            <h1 className="text-3xl font-bold text-gray-900 mb-8">Greenhouses</h1>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {greenhouses.map(gh => (
                    <Link
                        key={gh.id}
                        href={`/operator/greenhouses/${gh.id}`}
                        className="block bg-white rounded-xl shadow-sm border border-gray-200 hover:shadow-md hover:border-green-500 transition-all p-6"
                    >
                        <div className="flex justify-between items-start mb-4">
                            <div>
                                <h2 className="text-xl font-semibold text-gray-900">{gh.name}</h2>
                                <p className="text-sm text-gray-500">{gh.location || 'No location'}</p>
                            </div>
                            <span className="bg-green-100 text-green-800 text-xs px-2 py-1 rounded-full">
                                {gh.timezone}
                            </span>
                        </div>

                        <div className="grid grid-cols-3 gap-4 mt-6 border-t pt-4">
                            <div className="text-center">
                                <span className="block text-xl font-bold text-gray-700">{gh.device_count}</span>
                                <span className="text-xs text-gray-500 uppercase tracking-wide">Devices</span>
                            </div>
                            <div className="text-center border-l border-gray-100">
                                <span className="block text-xl font-bold text-gray-700">{gh.plant_count}</span>
                                <span className="text-xs text-gray-500 uppercase tracking-wide">Plants</span>
                            </div>
                            <div className="text-center border-l border-gray-100">
                                <span className="block text-xl font-bold text-gray-700">{gh.zone_count}</span>
                                <span className="text-xs text-gray-500 uppercase tracking-wide">Zones</span>
                            </div>
                        </div>
                    </Link>
                ))}
            </div>

            {greenhouses.length === 0 && (
                <div className="text-center py-12 bg-gray-50 rounded-xl border border-dashed border-gray-300">
                    <p className="text-gray-500">No greenhouses found.</p>
                </div>
            )}
        </main>
    );
}
