'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import {
    SpeciesDetail,
    TargetRange,
    Source,
    AuditEntry,
    METRIC_ORDER,
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
import { useRouter } from 'next/navigation';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface PlantDetailClientProps {
    id: string;
}

export default function PlantDetailClient({ id }: PlantDetailClientProps) {
    const router = useRouter();
    const { user } = useAuth();
    const [species, setSpecies] = useState<SpeciesDetail | null>(null);
    const [sources, setSources] = useState<Source[]>([]);
    const [history, setHistory] = useState<AuditEntry[]>([]);
    const [activeTab, setActiveTab] = useState<'conditions' | 'sources' | 'history'>('conditions');
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
            const speciesRes = await fetch(`${API_URL}/species/${id}`);
            if (!speciesRes.ok) throw new Error('Species not found');
            const speciesData = await speciesRes.json();
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
        // Reload history
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

    // Format history entry
    const formatHistoryEntry = (entry: AuditEntry): string => {
        const { action, entity_type, diff_json } = entry;
        const before = diff_json.before;
        const after = diff_json.after;

        if (entity_type === 'species') {
            if (action === 'CREATE') return 'Created plant';
            if (action === 'DELETE') return 'Deleted plant';
            if (action === 'UPDATE') {
                const changes: string[] = [];
                if (before && after) {
                    if (before.common_name !== after.common_name) changes.push(`name: ${before.common_name} → ${after.common_name}`);
                    if (before.latin_name !== after.latin_name) changes.push(`latin name changed`);
                    if (before.category !== after.category) changes.push(`category: ${before.category} → ${after.category}`);
                }
                return changes.length > 0 ? `Updated ${changes.join(', ')}` : 'Updated plant';
            }
        }

        if (entity_type === 'target_range') {
            if (action === 'CREATE') return `Added condition`;
            if (action === 'DELETE') return `Deleted condition`;
            if (action === 'UPDATE') {
                const changes: string[] = [];
                if (before && after) {
                    if (before.optimal_low !== after.optimal_low || before.optimal_high !== after.optimal_high) {
                        changes.push(`range: ${before.optimal_low}–${before.optimal_high} → ${after.optimal_low}–${after.optimal_high}`);
                    }
                    if (before.source_id !== after.source_id) changes.push('source changed');
                }
                return changes.length > 0 ? `Updated ${changes.join(', ')}` : 'Updated condition';
            }
        }

        return `${action} ${entity_type}`;
    };

    if (loading) {
        return (
            <div className="max-w-6xl mx-auto px-6 py-12">
                <div className="animate-pulse">
                    <div className="h-12 bg-gray-200 rounded w-1/3 mb-4"></div>
                    <div className="h-6 bg-gray-100 rounded w-1/4 mb-8"></div>
                    <div className="h-64 bg-gray-100 rounded-2xl"></div>
                </div>
            </div>
        );
    }

    if (error || !species) {
        return (
            <div className="max-w-6xl mx-auto px-6 py-12">
                <div className="bg-red-50 border border-red-200 rounded-xl p-6 text-center">
                    <p className="text-red-600">{error || 'Plant not found'}</p>
                    <Link href="/" className="mt-4 inline-block text-blue-600">← Back to plants</Link>
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

    // Get existing metric IDs
    const existingMetricIds = sortedTargets.map(t => t.metric_id);

    // Get unique sources for this plant
    const plantSourceIds = new Set(sortedTargets.map(t => t.source_id));
    const plantSources = sources.filter(s => plantSourceIds.has(s.id));

    return (
        <div className="max-w-6xl mx-auto px-6 py-12">
            {/* Breadcrumb */}
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

                {/* Plant Actions (only when logged in) */}
                {user ? (
                    <div className="flex gap-2">
                        <button
                            onClick={() => setShowEditPlant(true)}
                            className="px-3 py-2 text-sm text-gray-600 hover:text-gray-800 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
                        >
                            Edit Plant
                        </button>
                        <button
                            onClick={() => setShowDeletePlant(true)}
                            className="px-3 py-2 text-sm text-red-600 hover:text-red-800 bg-red-50 hover:bg-red-100 rounded-lg transition-colors"
                        >
                            Delete
                        </button>
                    </div>
                ) : (
                    <span className="text-sm text-gray-400">Login to edit</span>
                )}
            </header>

            {/* Tabs */}
            <div className="flex gap-2 mb-6 border-b border-gray-200 pb-4">
                {(['conditions', 'sources', 'history'] as const).map((tab) => (
                    <button
                        key={tab}
                        onClick={() => setActiveTab(tab)}
                        className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${activeTab === tab
                                ? 'bg-gray-800 text-white'
                                : 'text-gray-600 hover:bg-gray-100'
                            }`}
                    >
                        {tab === 'conditions' && 'Growing Conditions'}
                        {tab === 'sources' && `Sources (${plantSources.length})`}
                        {tab === 'history' && `History (${history.length})`}
                    </button>
                ))}
            </div>

            {/* Conditions Tab */}
            {activeTab === 'conditions' && (
                <div>
                    {/* Add Condition Button (only when logged in) */}
                    {user && (
                        <div className="flex justify-end mb-4">
                            <button
                                onClick={() => setShowAddCondition(true)}
                                className="px-4 py-2 text-sm font-medium text-blue-600 hover:text-blue-800 border border-blue-300 rounded-lg hover:bg-blue-50 transition-colors"
                            >
                                + Add Condition
                            </button>
                        </div>
                    )}

                    {/* Conditions Table */}
                    {sortedTargets.length > 0 ? (
                        <div className="bg-white border border-gray-200 rounded-2xl overflow-hidden">
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
                                                <span className="font-semibold text-green-600">
                                                    {target.optimal_low} – {target.optimal_high}
                                                </span>
                                                <span className="ml-1 text-sm text-gray-500">{target.metric.unit}</span>
                                            </td>
                                            <td className="px-6 py-4">
                                                {target.source.url ? (
                                                    <a
                                                        href={target.source.url}
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                        className="text-sm text-blue-600 hover:underline"
                                                    >
                                                        {target.source.publisher}
                                                    </a>
                                                ) : (
                                                    <span className="text-sm text-gray-500 italic">
                                                        {target.source.publisher}
                                                    </span>
                                                )}
                                            </td>
                                            {user && (
                                                <td className="px-6 py-4 text-right">
                                                    <button
                                                        onClick={() => setEditingCondition(target)}
                                                        className="px-2 py-1 text-xs text-gray-500 hover:text-gray-800 bg-gray-100 hover:bg-gray-200 rounded transition-colors mr-2"
                                                    >
                                                        Edit
                                                    </button>
                                                    <button
                                                        onClick={() => setDeletingCondition(target)}
                                                        className="px-2 py-1 text-xs text-red-500 hover:text-red-700 bg-red-50 hover:bg-red-100 rounded transition-colors"
                                                    >
                                                        Delete
                                                    </button>
                                                </td>
                                            )}
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    ) : (
                        <div className="text-center py-12 text-gray-500 bg-gray-50 rounded-2xl">
                            <p className="mb-4">No conditions defined yet</p>
                            {user && (
                                <button
                                    onClick={() => setShowAddCondition(true)}
                                    className="text-blue-600 hover:text-blue-800"
                                >
                                    Add your first condition
                                </button>
                            )}
                        </div>
                    )}
                </div>
            )}

            {/* Sources Tab */}
            {activeTab === 'sources' && (
                <div className="space-y-4">
                    {plantSources.length > 0 ? (
                        plantSources.map(source => (
                            <div key={source.id} className="bg-white border border-gray-200 rounded-xl p-5">
                                <h4 className="font-medium text-gray-800 mb-1">{source.title}</h4>
                                <p className="text-sm text-gray-500">
                                    {source.publisher}
                                    {source.year && ` • ${source.year}`}
                                </p>
                                {source.url && (
                                    <a
                                        href={source.url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="inline-block mt-3 text-sm text-blue-600 hover:underline"
                                    >
                                        View Source →
                                    </a>
                                )}
                            </div>
                        ))
                    ) : (
                        <div className="text-center py-12 text-gray-500 bg-gray-50 rounded-2xl">
                            No sources yet. Add conditions to include sources.
                        </div>
                    )}
                </div>
            )}

            {/* History Tab */}
            {activeTab === 'history' && (
                <div className="space-y-2">
                    {history.length > 0 ? (
                        history.map(entry => (
                            <div key={entry.id} className="flex items-start gap-4 py-3 border-b border-gray-100 last:border-0">
                                <div className="text-xs text-gray-400 whitespace-nowrap pt-0.5">
                                    {new Date(entry.timestamp).toLocaleString('de-CH', {
                                        day: '2-digit',
                                        month: '2-digit',
                                        year: 'numeric',
                                        hour: '2-digit',
                                        minute: '2-digit'
                                    })}
                                </div>
                                <div className="flex-1">
                                    <span className="text-sm text-gray-800">{formatHistoryEntry(entry)}</span>
                                    {entry.user_email && (
                                        <span className="ml-2 text-xs text-gray-400">— {entry.user_email}</span>
                                    )}
                                </div>
                                <span className={`px-2 py-0.5 text-xs rounded ${entry.action === 'CREATE' ? 'bg-green-100 text-green-700' :
                                        entry.action === 'UPDATE' ? 'bg-blue-100 text-blue-700' :
                                            'bg-red-100 text-red-700'
                                    }`}>
                                    {entry.action}
                                </span>
                            </div>
                        ))
                    ) : (
                        <div className="text-center py-12 text-gray-500 bg-gray-50 rounded-2xl">
                            No changes recorded yet.
                        </div>
                    )}
                </div>
            )}

            {/* Add Condition Modal */}
            <Modal isOpen={showAddCondition} onClose={() => setShowAddCondition(false)} title="Add Condition">
                <ConditionForm
                    speciesId={parseInt(id)}
                    existingMetricIds={existingMetricIds}
                    onSuccess={handleConditionCreated}
                    onCancel={() => setShowAddCondition(false)}
                />
            </Modal>

            {/* Edit Condition Modal */}
            <Modal isOpen={!!editingCondition} onClose={() => setEditingCondition(null)} title="Edit Condition">
                {editingCondition && (
                    <ConditionForm
                        speciesId={parseInt(id)}
                        condition={editingCondition}
                        onSuccess={handleConditionUpdated}
                        onCancel={() => setEditingCondition(null)}
                    />
                )}
            </Modal>

            {/* Delete Condition Confirmation */}
            <ConfirmDialog
                isOpen={!!deletingCondition}
                onClose={() => { setDeletingCondition(null); setActionError(null); }}
                onConfirm={handleDeleteCondition}
                title="Delete Condition"
                message={actionError || `Delete the ${deletingCondition?.metric.label} condition?`}
                confirmLabel="Delete"
                danger
            />

            {/* Edit Plant Modal */}
            <Modal isOpen={showEditPlant} onClose={() => setShowEditPlant(false)} title="Edit Plant">
                <PlantForm
                    plant={species}
                    onSuccess={handlePlantUpdated}
                    onCancel={() => setShowEditPlant(false)}
                />
            </Modal>

            {/* Delete Plant Confirmation */}
            <ConfirmDialog
                isOpen={showDeletePlant}
                onClose={() => { setShowDeletePlant(false); setActionError(null); }}
                onConfirm={handleDeletePlant}
                title="Delete Plant"
                message={actionError || `Are you sure you want to delete "${species.common_name}"? All conditions will also be deleted.`}
                confirmLabel="Delete Plant"
                danger
            />
        </div>
    );
}
