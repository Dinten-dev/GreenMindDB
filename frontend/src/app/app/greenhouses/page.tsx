'use client';

import { useState, useEffect } from 'react';
import { apiListGreenhouses, apiCreateGreenhouse, Greenhouse } from '@/lib/api';

export default function GreenhousesPage() {
    const [greenhouses, setGreenhouses] = useState<Greenhouse[]>([]);
    const [loading, setLoading] = useState(true);
    const [showCreate, setShowCreate] = useState(false);
    const [name, setName] = useState('');
    const [location, setLocation] = useState('');
    const [creating, setCreating] = useState(false);

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

    if (loading) {
        return (
            <div className="animate-pulse space-y-4">
                <div className="h-8 w-40 bg-apple-gray-200 rounded-lg" />
                <div className="h-32 bg-apple-gray-200 rounded-apple-lg" />
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-apple-gray-800">Greenhouses</h1>
                    <p className="text-sm text-apple-gray-400 mt-1">Manage your growing facilities</p>
                </div>
                <button
                    onClick={() => setShowCreate(true)}
                    className="px-4 py-2 bg-gm-green-500 text-white rounded-full text-sm font-medium hover:bg-gm-green-600 transition-colors"
                >
                    + New Greenhouse
                </button>
            </div>

            {/* Create Modal */}
            {showCreate && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
                    <div className="bg-white rounded-apple-xl shadow-apple-lg p-8 w-full max-w-md mx-4">
                        <h2 className="text-xl font-semibold text-apple-gray-800 mb-6">New Greenhouse</h2>
                        <form onSubmit={handleCreate} className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-apple-gray-600 mb-1.5">Name</label>
                                <input
                                    type="text"
                                    required
                                    value={name}
                                    onChange={(e) => setName(e.target.value)}
                                    className="w-full px-4 py-2.5 rounded-apple bg-apple-gray-100 border border-apple-gray-200 text-sm text-apple-gray-800 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:border-transparent"
                                    placeholder="e.g. Main Greenhouse"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-apple-gray-600 mb-1.5">Location (optional)</label>
                                <input
                                    type="text"
                                    value={location}
                                    onChange={(e) => setLocation(e.target.value)}
                                    className="w-full px-4 py-2.5 rounded-apple bg-apple-gray-100 border border-apple-gray-200 text-sm text-apple-gray-800 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:border-transparent"
                                    placeholder="e.g. Basel, Switzerland"
                                />
                            </div>
                            <div className="flex gap-3 pt-2">
                                <button
                                    type="button"
                                    onClick={() => setShowCreate(false)}
                                    className="flex-1 py-2.5 bg-apple-gray-100 text-apple-gray-600 rounded-apple text-sm font-medium hover:bg-apple-gray-200 transition-colors"
                                >
                                    Cancel
                                </button>
                                <button
                                    type="submit"
                                    disabled={creating}
                                    className="flex-1 py-2.5 bg-gm-green-500 text-white rounded-apple text-sm font-medium hover:bg-gm-green-600 transition-colors disabled:opacity-50"
                                >
                                    {creating ? 'Creating…' : 'Create'}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* Greenhouse List */}
            {greenhouses.length === 0 ? (
                <div className="bg-white rounded-apple-lg shadow-apple-card p-12 text-center">
                    <div className="text-4xl mb-4">🏠</div>
                    <h3 className="text-lg font-semibold text-apple-gray-800 mb-2">No greenhouses yet</h3>
                    <p className="text-sm text-apple-gray-400 mb-4">
                        Create your first greenhouse to start adding devices.
                    </p>
                    <button
                        onClick={() => setShowCreate(true)}
                        className="px-6 py-2.5 bg-gm-green-500 text-white rounded-full text-sm font-medium hover:bg-gm-green-600 transition-colors"
                    >
                        Create Greenhouse
                    </button>
                </div>
            ) : (
                <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {greenhouses.map(gh => (
                        <div key={gh.id} className="bg-white rounded-apple-lg shadow-apple-card p-6 hover:shadow-apple transition-shadow duration-300">
                            <h3 className="text-lg font-semibold text-apple-gray-800 mb-1">{gh.name}</h3>
                            {gh.location && <p className="text-sm text-apple-gray-400 mb-4">{gh.location}</p>}
                            <div className="grid grid-cols-2 gap-3 text-sm">
                                <div className="bg-apple-gray-100/50 rounded-apple px-3 py-2">
                                    <p className="text-apple-gray-400 text-xs">Devices</p>
                                    <p className="font-semibold text-apple-gray-800">{gh.device_count}</p>
                                </div>
                                <div className="bg-apple-gray-100/50 rounded-apple px-3 py-2">
                                    <p className="text-apple-gray-400 text-xs">Sensors</p>
                                    <p className="font-semibold text-apple-gray-800">{gh.sensor_count}</p>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
