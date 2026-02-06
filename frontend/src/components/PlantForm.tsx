'use client';

import { useState } from 'react';
import { Species, createSpecies, updateSpecies } from '@/lib/api';

interface PlantFormProps {
    plant?: Species;  // If provided, edit mode
    onSuccess: (plant: Species) => void;
    onCancel: () => void;
}

export default function PlantForm({ plant, onSuccess, onCancel }: PlantFormProps) {
    const [name, setName] = useState(plant?.common_name || '');
    const [latinName, setLatinName] = useState(plant?.latin_name || '');
    const [category, setCategory] = useState(plant?.category || 'Unknown');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const isEdit = !!plant;

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError(null);
        setLoading(true);

        try {
            let result: Species;
            if (isEdit) {
                result = await updateSpecies(plant.id, {
                    common_name: name,
                    latin_name: latinName,
                    category: category
                });
            } else {
                result = await createSpecies({
                    common_name: name,
                    latin_name: latinName,
                    category: category
                });
            }
            onSuccess(result);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'An error occurred');
        } finally {
            setLoading(false);
        }
    };

    return (
        <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
                <div className="bg-red-50 border border-red-200 text-red-600 px-4 py-2 rounded-lg text-sm">
                    {error}
                </div>
            )}

            <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Name *</label>
                <input
                    type="text"
                    value={name}
                    onChange={e => setName(e.target.value)}
                    required
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none"
                    placeholder="e.g., Tomato"
                />
            </div>

            <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Latin Name</label>
                <input
                    type="text"
                    value={latinName}
                    onChange={e => setLatinName(e.target.value)}
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none"
                    placeholder="e.g., Solanum lycopersicum"
                />
            </div>

            <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Category</label>
                <select
                    value={category}
                    onChange={e => setCategory(e.target.value)}
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none bg-white"
                >
                    <option value="Unknown">Unknown</option>
                    <option value="Fruit">Fruit</option>
                    <option value="Leafy Green">Leafy Green</option>
                    <option value="Root Vegetable">Root Vegetable</option>
                    <option value="Herb">Herb</option>
                    <option value="Flower">Flower</option>
                </select>
            </div>

            <div className="flex gap-3 justify-end pt-4">
                <button
                    type="button"
                    onClick={onCancel}
                    className="px-4 py-2 text-sm font-medium text-gray-600 hover:text-gray-800 transition-colors"
                >
                    Cancel
                </button>
                <button
                    type="submit"
                    disabled={loading || !name.trim()}
                    className="px-4 py-2 text-sm font-medium bg-gray-800 hover:bg-gray-900 text-white rounded-lg transition-colors disabled:opacity-50"
                >
                    {loading ? 'Saving...' : (isEdit ? 'Save Changes' : 'Add Plant')}
                </button>
            </div>
        </form>
    );
}
