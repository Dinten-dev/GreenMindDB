'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { Species, fetchSpecies, deleteSpecies } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import Modal from '@/components/Modal';
import PlantForm from '@/components/PlantForm';
import ConfirmDialog from '@/components/ConfirmDialog';

export default function HomePageClient() {
    const { user } = useAuth();
    const [species, setSpecies] = useState<Species[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Modal states
    const [showAddModal, setShowAddModal] = useState(false);
    const [editingPlant, setEditingPlant] = useState<Species | null>(null);
    const [deletingPlant, setDeletingPlant] = useState<Species | null>(null);
    const [deleteError, setDeleteError] = useState<string | null>(null);

    const loadSpecies = async () => {
        try {
            const data = await fetchSpecies();
            setSpecies(data);
        } catch (e) {
            setError('Failed to load plants');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadSpecies();
    }, []);

    const handlePlantCreated = (plant: Species) => {
        setSpecies(prev => [...prev, plant].sort((a, b) => a.common_name.localeCompare(b.common_name)));
        setShowAddModal(false);
    };

    const handlePlantUpdated = (plant: Species) => {
        setSpecies(prev => prev.map(p => p.id === plant.id ? plant : p).sort((a, b) => a.common_name.localeCompare(b.common_name)));
        setEditingPlant(null);
    };

    const handleDeletePlant = async () => {
        if (!deletingPlant) return;
        setDeleteError(null);
        try {
            await deleteSpecies(deletingPlant.id);
            setSpecies(prev => prev.filter(p => p.id !== deletingPlant.id));
            setDeletingPlant(null);
        } catch (err) {
            setDeleteError(err instanceof Error ? err.message : 'Failed to delete');
        }
    };

    if (loading) {
        return (
            <div className="max-w-6xl mx-auto px-6 py-12">
                <div className="animate-pulse grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {[1, 2, 3].map(i => (
                        <div key={i} className="h-48 bg-gray-100 rounded-2xl" />
                    ))}
                </div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="max-w-6xl mx-auto px-6 py-12">
                <div className="bg-red-50 border border-red-200 text-red-600 rounded-xl p-6 text-center">
                    {error}
                </div>
            </div>
        );
    }

    return (
        <div className="max-w-6xl mx-auto px-6 py-12">
            {/* Header with Add Button */}
            <div className="flex items-center justify-between mb-8">
                <div>
                    <h1 className="text-4xl font-semibold text-gray-800">Plants</h1>
                    <p className="text-gray-500 mt-1">
                        {user ? 'Manage plant growing conditions' : 'Browse plant growing conditions'}
                    </p>
                </div>
                {user ? (
                    <button
                        onClick={() => setShowAddModal(true)}
                        className="px-4 py-2 bg-gray-800 hover:bg-gray-900 text-white text-sm font-medium rounded-lg transition-colors flex items-center gap-2"
                    >
                        <span className="text-lg">+</span> Add Plant
                    </button>
                ) : (
                    <span className="text-sm text-gray-400">Login to edit</span>
                )}
            </div>

            {/* Plant Grid */}
            {species.length === 0 ? (
                <div className="text-center py-16 text-gray-500">
                    <p className="mb-4">No plants yet</p>
                    {user && (
                        <button
                            onClick={() => setShowAddModal(true)}
                            className="text-blue-600 hover:text-blue-800"
                        >
                            Add your first plant
                        </button>
                    )}
                </div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {species.map(plant => (
                        <div
                            key={plant.id}
                            className="bg-white border border-gray-200 rounded-2xl p-6 hover:shadow-lg transition-shadow group relative"
                        >
                            {/* Edit/Delete Buttons (only when logged in) */}
                            {user && (
                                <div className="absolute top-4 right-4 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                                    <button
                                        onClick={(e) => { e.preventDefault(); setEditingPlant(plant); }}
                                        className="px-2 py-1 text-xs text-gray-500 hover:text-gray-800 bg-gray-100 hover:bg-gray-200 rounded transition-colors"
                                    >
                                        Edit
                                    </button>
                                    <button
                                        onClick={(e) => { e.preventDefault(); setDeletingPlant(plant); }}
                                        className="px-2 py-1 text-xs text-red-500 hover:text-red-700 bg-red-50 hover:bg-red-100 rounded transition-colors"
                                    >
                                        Delete
                                    </button>
                                </div>
                            )}

                            <Link href={`/plants/${plant.id}`} className="block">
                                <h2 className="text-xl font-semibold text-gray-800 mb-1">
                                    {plant.common_name}
                                </h2>
                                <p className="text-gray-400 italic text-sm mb-3">
                                    {plant.latin_name}
                                </p>
                                <span className="inline-block px-3 py-1 bg-gray-100 text-gray-600 text-xs font-medium rounded-full">
                                    {plant.category}
                                </span>
                                {plant.notes && (
                                    <p className="mt-4 text-gray-500 text-sm line-clamp-2">
                                        {plant.notes}
                                    </p>
                                )}
                            </Link>
                        </div>
                    ))}
                </div>
            )}

            {/* Add Plant Modal */}
            <Modal isOpen={showAddModal} onClose={() => setShowAddModal(false)} title="Add New Plant">
                <PlantForm
                    onSuccess={handlePlantCreated}
                    onCancel={() => setShowAddModal(false)}
                />
            </Modal>

            {/* Edit Plant Modal */}
            <Modal isOpen={!!editingPlant} onClose={() => setEditingPlant(null)} title="Edit Plant">
                {editingPlant && (
                    <PlantForm
                        plant={editingPlant}
                        onSuccess={handlePlantUpdated}
                        onCancel={() => setEditingPlant(null)}
                    />
                )}
            </Modal>

            {/* Delete Confirmation */}
            <ConfirmDialog
                isOpen={!!deletingPlant}
                onClose={() => { setDeletingPlant(null); setDeleteError(null); }}
                onConfirm={handleDeletePlant}
                title="Delete Plant"
                message={deleteError || `Are you sure you want to delete "${deletingPlant?.common_name}"? This cannot be undone.`}
                confirmLabel="Delete"
                danger
            />
        </div>
    );
}
