'use client';

import { useEffect, useState } from 'react';
import { fetchGreenhouses, createGreenhouse, Greenhouse, GreenhouseCreate } from '@/lib/api';
import Link from 'next/link';

export default function AdminGreenhousesPage() {
    const [greenhouses, setGreenhouses] = useState<Greenhouse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Form state
    const [showForm, setShowForm] = useState(false);
    const [formData, setFormData] = useState<GreenhouseCreate>({ name: '', location: '', timezone: 'UTC' });
    const [creating, setCreating] = useState(false);

    useEffect(() => {
        loadGreenhouses();
    }, []);

    const loadGreenhouses = () => {
        setLoading(true);
        fetchGreenhouses()
            .then(setGreenhouses)
            .catch(err => setError(err.message))
            .finally(() => setLoading(false));
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setCreating(true);
        setError(null);
        try {
            await createGreenhouse(formData);
            setFormData({ name: '', location: '', timezone: 'UTC' });
            setShowForm(false);
            loadGreenhouses();
        } catch (err: any) {
            setError(err.message);
        } finally {
            setCreating(false);
        }
    };

    return (
        <main className="max-w-7xl mx-auto px-6 py-8">
            <div className="flex justify-between items-center mb-8">
                <h1 className="text-3xl font-bold text-gray-900">Greenhouse Administration</h1>
                <button
                    onClick={() => setShowForm(!showForm)}
                    className="bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded-lg font-medium transition-colors"
                >
                    {showForm ? 'Cancel' : '+ New Greenhouse'}
                </button>
            </div>

            {error && <div className="bg-red-50 text-red-700 p-4 rounded-xl mb-6 border border-red-200">{error}</div>}

            {showForm && (
                <div className="bg-white p-6 rounded-xl shadow-md border border-gray-100 mb-8 max-w-2xl">
                    <h2 className="text-xl font-bold text-gray-900 mb-4">Create New Greenhouse</h2>
                    <form onSubmit={handleSubmit} className="space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                            <input
                                type="text"
                                required
                                value={formData.name}
                                onChange={e => setFormData({ ...formData, name: e.target.value })}
                                className="w-full border-gray-300 rounded-md shadow-sm focus:border-green-500 focus:ring-green-500"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">Location</label>
                            <input
                                type="text"
                                value={formData.location || ''}
                                onChange={e => setFormData({ ...formData, location: e.target.value })}
                                className="w-full border-gray-300 rounded-md shadow-sm focus:border-green-500 focus:ring-green-500"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">Timezone</label>
                            <select
                                value={formData.timezone}
                                onChange={e => setFormData({ ...formData, timezone: e.target.value })}
                                className="w-full border-gray-300 rounded-md shadow-sm focus:border-green-500 focus:ring-green-500"
                            >
                                <option value="UTC">UTC</option>
                                <option value="Europe/Zurich">Europe/Zurich</option>
                                <option value="US/Pacific">US/Pacific</option>
                                {/* Add more as needed */}
                            </select>
                        </div>
                        <div className="flex justify-end pt-2">
                            <button
                                type="submit"
                                disabled={creating}
                                className="bg-green-600 hover:bg-green-700 text-white px-6 py-2 rounded-lg font-medium transition-colors disabled:opacity-50"
                            >
                                {creating ? 'Creating...' : 'Create Greenhouse'}
                            </button>
                        </div>
                    </form>
                </div>
            )}

            {loading ? (
                <div className="text-center py-12 text-gray-500">Loading...</div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {greenhouses.map(gh => (
                        <Link
                            key={gh.id}
                            href={`/admin/greenhouses/${gh.id}`}
                            className="block bg-white rounded-xl shadow-sm border border-gray-200 hover:shadow-md hover:border-green-500 transition-all p-6 group"
                        >
                            <div className="flex justify-between items-start mb-4">
                                <h2 className="text-xl font-semibold text-gray-900 group-hover:text-green-700 transition-colors">{gh.name}</h2>
                                <span className="text-xs text-gray-500 bg-gray-100 px-2 py-1 rounded-full">{gh.timezone}</span>
                            </div>
                            <p className="text-sm text-gray-500 mb-4">{gh.location || 'No active location set'}</p>
                            <div className="flex items-center gap-4 text-sm text-gray-600 pt-4 border-t border-gray-50">
                                <span>{gh.device_count} devices</span>
                                <span>{gh.plant_count} plants</span>
                            </div>
                        </Link>
                    ))}
                    {greenhouses.length === 0 && !loading && (
                        <div className="col-span-full text-center py-12 bg-gray-50 rounded-xl border border-dashed border-gray-300">
                            <p className="text-gray-500">No greenhouses found. Create one to get started.</p>
                        </div>
                    )}
                </div>
            )}
        </main>
    );
}
