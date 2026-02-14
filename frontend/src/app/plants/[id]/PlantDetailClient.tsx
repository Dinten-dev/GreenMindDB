'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import {
    SpeciesDetail,
    TargetRange,
    Source,
    AuditEntry,
    METRIC_ORDER,
    fetchSpeciesDetail,
    fetchSources,
    fetchSpeciesHistory,
    deleteTargetRange,
    deleteSpecies
} from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import Modal from '@/components/Modal';
import ConditionForm from '@/components/ConditionForm';
import PlantForm from '@/components/PlantForm';
import ConfirmDialog from '@/components/ConfirmDialog';
import LiveDataSection from '@/components/LiveDataSection';
import { useRouter } from 'next/navigation';

interface PlantDetailClientProps {
    id: string;
}

export default function PlantDetailClient({ id }: PlantDetailClientProps) {
    const router = useRouter();
    const { user } = useAuth();
    const [species, setSpecies] = useState<SpeciesDetail | null>(null);
    const [sources, setSources] = useState<Source[]>([]);
    const [history, setHistory] = useState<AuditEntry[]>([]);

    // Tabs for conditions view
    const [activeTab, setActiveTab] = useState<'details' | 'sources' | 'history'>('details');

    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Modal states
    const [showAddCondition, setShowAddCondition] = useState(false);
    const [editingCondition, setEditingCondition] = useState<TargetRange | null>(null);
    const [deletingCondition, setDeletingCondition] = useState<TargetRange | null>(null);
    const [showEditPlant, setShowEditPlant] = useState(false);
    const [showDeletePlant, setShowDeletePlant] = useState(false);
    const [actionError, setActionError] = useState<string | null>(null);

    const loadData = async () => {
        try {
            setLoading(true);
            const speciesData = await fetchSpeciesDetail(parseInt(id));
            setSpecies(speciesData);

            const sourcesData = await fetchSources();
            setSources(sourcesData);

            const historyData = await fetchSpeciesHistory(parseInt(id));
            setHistory(historyData);
        } catch (e) {
            setError('Failed to load plant data');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadData();
    }, [id]);

    const handleConditionCreated = (condition: TargetRange) => {
        if (species) {
            setSpecies({
                ...species,
                target_ranges: [...species.target_ranges, condition]
            });
        }
        setShowAddCondition(false);
        fetchSpeciesHistory(parseInt(id)).then(setHistory);
    };

    const handleConditionUpdated = (condition: TargetRange) => {
        if (species) {
            setSpecies({
                ...species,
                target_ranges: species.target_ranges.map(t => t.id === condition.id ? condition : t)
            });
        }
        setEditingCondition(null);
        fetchSpeciesHistory(parseInt(id)).then(setHistory);
    };

    const handleDeleteCondition = async () => {
        if (!deletingCondition) return;
        setActionError(null);
        try {
            await deleteTargetRange(deletingCondition.id);
            if (species) {
                setSpecies({
                    ...species,
                    target_ranges: species.target_ranges.filter(t => t.id !== deletingCondition.id)
                });
            }
            setDeletingCondition(null);
            fetchSpeciesHistory(parseInt(id)).then(setHistory);
        } catch (err) {
            setActionError(err instanceof Error ? err.message : 'Failed to delete');
        }
    };

    const handlePlantUpdated = (plant: { id: number; common_name: string; latin_name: string; category: string; notes: string | null }) => {
        if (species) {
            setSpecies({
                ...species,
                common_name: plant.common_name,
                latin_name: plant.latin_name,
                category: plant.category,
                notes: plant.notes
            });
        }
        setShowEditPlant(false);
        fetchSpeciesHistory(parseInt(id)).then(setHistory);
    };

    const handleDeletePlant = async () => {
        setActionError(null);
        try {
            await deleteSpecies(parseInt(id));
            router.push('/');
        } catch (err) {
            setActionError(err instanceof Error ? err.message : 'Failed to delete plant');
        }
    };

    const formatHistoryEntry = (entry: AuditEntry): string => {
        const { action, entity_type } = entry;

        if (entity_type === 'species') {
            if (action === 'CREATE') return 'Created plant';
            if (action === 'DELETE') return 'Deleted plant';
            if (action === 'UPDATE') return 'Updated plant info';
        }
        if (entity_type === 'target_range') {
            if (action === 'CREATE') return `Added ${entry.diff_json?.after?.metric_key || 'condition'}`;
            return `${action} condition`;
        }
        return `${action} ${entity_type}`;
    };

    if (loading) {
        return (
            <div className="max-w-6xl mx-auto px-6 py-12">
                <div className="animate-pulse">
                    <div className="h-12 bg-gray-200 rounded w-1/3 mb-4"></div>
                </div>
            </div>
        );
    }

    if (error || !species) {
        return (
            <div className="max-w-6xl mx-auto px-6 py-12">
                <div className="bg-red-50 p-6 text-center rounded-xl text-red-600">
                    {error || 'Plant not found'}
                </div>
            </div>
        );
    }

    // Sort targets by metric order
    const sortedTargets = [...(species.target_ranges || [])].sort((a, b) => {
        const aIndex = METRIC_ORDER.indexOf(a.metric.key);
        const bIndex = METRIC_ORDER.indexOf(b.metric.key);
        return aIndex - bIndex;
    });
    const existingMetricIds = sortedTargets.map(t => t.metric_id);
    const plantSourceIds = new Set(sortedTargets.map(t => t.source_id));
    const plantSources = sources.filter(s => plantSourceIds.has(s.id));

    return (
        <div className="max-w-6xl mx-auto px-6 py-12">
            {/* Nav */}
            <nav className="mb-6 text-sm text-gray-500">
                <Link href="/" className="hover:text-blue-600">Plants</Link>
                <span className="mx-2">/</span>
                <span className="text-gray-800">{species.common_name}</span>
            </nav>

            {/* Header */}
            <header className="mb-8 flex justify-between items-start">
                <div>
                    <h1 className="text-4xl font-semibold text-gray-800">
                        {species.common_name}
                    </h1>
                    <p className="text-xl text-gray-500 italic mt-1">
                        {species.latin_name}
                    </p>
                    <span className="inline-block mt-3 px-4 py-1.5 bg-gray-100 text-gray-600 text-sm font-medium rounded-full">
                        {species.category}
                    </span>
                </div>
                {user && (
                    <div className="flex gap-2">
                        <button onClick={() => setShowEditPlant(true)} className="px-3 py-2 text-sm text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200">
                            Edit
                        </button>
                        <button onClick={() => setShowDeletePlant(true)} className="px-3 py-2 text-sm text-red-600 bg-red-50 rounded-lg hover:bg-red-100">
                            Delete
                        </button>
                    </div>
                )}
            </header>

            {/* Secondary Tabs */}
            <div className="flex gap-6 mb-6 border-b border-gray-200">
                <button
                    onClick={() => setActiveTab('details')}
                    className={`pb-3 text-sm font-medium border-b-2 transition-colors ${activeTab === 'details' ? 'border-emerald-600 text-emerald-800' : 'border-transparent text-gray-500'}`}
                >
                    Growing Conditions
                </button>
                <button
                    onClick={() => setActiveTab('sources')}
                    className={`pb-3 text-sm font-medium border-b-2 transition-colors ${activeTab === 'sources' ? 'border-emerald-600 text-emerald-800' : 'border-transparent text-gray-500'}`}
                >
                    Sources ({plantSources.length})
                </button>
                <button
                    onClick={() => setActiveTab('history')}
                    className={`pb-3 text-sm font-medium border-b-2 transition-colors ${activeTab === 'history' ? 'border-emerald-600 text-emerald-800' : 'border-transparent text-gray-500'}`}
                >
                    History
                </button>
            </div>

            {/* Tab Content */}
            {activeTab === 'details' && (
                <div>
                    {user && (
                        <div className="flex justify-end mb-4">
                            <button
                                onClick={() => setShowAddCondition(true)}
                                className="px-4 py-2 text-sm font-medium text-white bg-emerald-600 rounded-lg hover:bg-emerald-700 transition-colors shadow-sm"
                            >
                                + Add Condition
                            </button>
                        </div>
                    )}

                    {sortedTargets.length > 0 ? (
                        <div className="bg-white border border-gray-200 rounded-2xl overflow-hidden shadow-sm">
                            <table className="w-full">
                                <thead className="bg-gray-50 border-b border-gray-200">
                                    <tr>
                                        <th className="text-left px-6 py-4 text-sm font-medium text-gray-600">Metric</th>
                                        <th className="text-center px-6 py-4 text-sm font-medium text-gray-600">Optimal Range</th>
                                        <th className="text-left px-6 py-4 text-sm font-medium text-gray-600">Source</th>
                                        {user && <th className="text-right px-6 py-4 text-sm font-medium text-gray-600">Actions</th>}
                                    </tr>
                                </thead>
                                <tbody>
                                    {sortedTargets.map((target, idx) => (
                                        <tr key={target.id} className={idx < sortedTargets.length - 1 ? 'border-b border-gray-100' : ''}>
                                            <td className="px-6 py-4">
                                                <div className="font-medium text-gray-800">{target.metric.label}</div>
                                                <div className="text-xs text-gray-400">{target.metric.key}</div>
                                            </td>
                                            <td className="px-6 py-4 text-center">
                                                <span className="font-semibold text-emerald-600 bg-emerald-50 px-2.5 py-1 rounded-full text-sm">
                                                    {target.optimal_low} – {target.optimal_high} <span className="text-xs">{target.metric.unit}</span>
                                                </span>
                                            </td>
                                            <td className="px-6 py-4">
                                                <span className="text-sm text-gray-600">{target.source.publisher}</span>
                                            </td>
                                            {user && (
                                                <td className="px-6 py-4 text-right">
                                                    <button onClick={() => setEditingCondition(target)} className="text-gray-400 hover:text-emerald-600 mr-3">Edit</button>
                                                    <button onClick={() => setDeletingCondition(target)} className="text-gray-400 hover:text-red-600">Delete</button>
                                                </td>
                                            )}
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    ) : (
                        <div className="text-center py-12 text-gray-500 bg-gray-50/50 rounded-2xl border border-dashed border-gray-200">
                            <p>No growing conditions defined.</p>
                        </div>
                    )}
                </div>
            )}

            {activeTab === 'sources' && (
                <div className="space-y-4">
                    {plantSources.map(source => (
                        <div key={source.id} className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm">
                            <h4 className="font-medium text-gray-800">{source.title}</h4>
                            <p className="text-sm text-gray-500 mt-1">{source.publisher} • {source.year}</p>
                        </div>
                    ))}
                    {plantSources.length === 0 && <div className="text-center text-gray-500 py-8">No linked sources.</div>}
                </div>
            )}

            {activeTab === 'history' && (
                <div className="space-y-2">
                    {history.map(entry => (
                        <div key={entry.id} className="py-3 border-b border-gray-100 last:border-0 text-sm">
                            <div className="text-gray-400 text-xs">{new Date(entry.timestamp).toLocaleString()}</div>
                            <div className="text-gray-700 mt-0.5">{formatHistoryEntry(entry)}</div>
                        </div>
                    ))}
                    {history.length === 0 && <div className="text-center text-gray-500 py-8">No history.</div>}
                </div>
            )}

            {/* Live Data Accordion - Always visible below main content */}
            <LiveDataSection speciesId={id} />

            {/* Modals */}
            <Modal isOpen={showAddCondition} onClose={() => setShowAddCondition(false)} title="Add Condition">
                <ConditionForm speciesId={parseInt(id)} existingMetricIds={existingMetricIds} onSuccess={handleConditionCreated} onCancel={() => setShowAddCondition(false)} />
            </Modal>
            <Modal isOpen={!!editingCondition} onClose={() => setEditingCondition(null)} title="Edit Condition">
                {editingCondition && <ConditionForm speciesId={parseInt(id)} condition={editingCondition} onSuccess={handleConditionUpdated} onCancel={() => setEditingCondition(null)} />}
            </Modal>
            <ConfirmDialog isOpen={!!deletingCondition} onClose={() => setDeletingCondition(null)} onConfirm={handleDeleteCondition} title="Delete Condition" message={`Remove this condition?`} confirmLabel="Delete" danger />
            <Modal isOpen={showEditPlant} onClose={() => setShowEditPlant(false)} title="Edit Plant">
                <PlantForm plant={species} onSuccess={handlePlantUpdated} onCancel={() => setShowEditPlant(false)} />
            </Modal>
            <ConfirmDialog isOpen={showDeletePlant} onClose={() => setShowDeletePlant(false)} onConfirm={handleDeletePlant} title="Delete Plant" message={`Delete ${species.common_name}?`} confirmLabel="Delete" danger />
        </div>
    );
}
