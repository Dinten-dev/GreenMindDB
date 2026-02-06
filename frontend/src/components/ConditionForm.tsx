'use client';

import { useState, useEffect } from 'react';
import {
    TargetRange,
    Metric,
    SourceInput,
    fetchMetrics,
    createTargetRange,
    updateTargetRange
} from '@/lib/api';

interface ConditionFormProps {
    speciesId: number;
    condition?: TargetRange;
    existingMetricIds?: number[];
    onSuccess: (condition: TargetRange) => void;
    onCancel: () => void;
}

export default function ConditionForm({
    speciesId,
    condition,
    existingMetricIds = [],
    onSuccess,
    onCancel
}: ConditionFormProps) {
    const [metrics, setMetrics] = useState<Metric[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Form state
    const [metricId, setMetricId] = useState<number>(condition?.metric_id || 0);
    const [optimalLow, setOptimalLow] = useState<string>(condition?.optimal_low?.toString() || '');
    const [optimalHigh, setOptimalHigh] = useState<string>(condition?.optimal_high?.toString() || '');

    // Source state (new UX)
    const [sourceType, setSourceType] = useState<'url' | 'own_experience'>(
        condition?.source?.publisher === 'User' ? 'own_experience' : 'url'
    );
    const [sourceUrl, setSourceUrl] = useState<string>(condition?.source?.url || '');
    const [sourceTitle, setSourceTitle] = useState<string>(condition?.source?.title || '');
    const [sourcePublisher, setSourcePublisher] = useState<string>(condition?.source?.publisher || '');
    const [sourceYear, setSourceYear] = useState<string>(condition?.source?.year?.toString() || '');
    const [sourceNotes, setSourceNotes] = useState<string>(condition?.source?.notes || '');

    useEffect(() => {
        fetchMetrics().then(setMetrics);
    }, []);

    const availableMetrics = condition
        ? metrics  // When editing, show all metrics
        : metrics.filter(m => !existingMetricIds.includes(m.id));  // When creating, exclude existing

    const isFormValid =
        (condition || metricId > 0) &&
        optimalLow !== '' &&
        optimalHigh !== '' &&
        parseFloat(optimalHigh) >= parseFloat(optimalLow) &&
        (sourceType === 'own_experience' || sourceUrl.trim().length > 0);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError(null);
        setLoading(true);

        const source: SourceInput = {
            source_type: sourceType,
            url: sourceType === 'url' ? sourceUrl : undefined,
            title: sourceTitle || undefined,
            publisher: sourcePublisher || undefined,
            year: sourceYear ? parseInt(sourceYear) : undefined,
            notes: sourceNotes || undefined
        };

        try {
            if (condition) {
                const updated = await updateTargetRange(condition.id, {
                    optimal_low: parseFloat(optimalLow),
                    optimal_high: parseFloat(optimalHigh),
                    source
                });
                onSuccess(updated);
            } else {
                const created = await createTargetRange({
                    species_id: speciesId,
                    metric_id: metricId,
                    optimal_low: parseFloat(optimalLow),
                    optimal_high: parseFloat(optimalHigh),
                    source
                });
                onSuccess(created);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'An error occurred');
        } finally {
            setLoading(false);
        }
    };

    const selectedMetric = metrics.find(m => m.id === metricId) || condition?.metric;

    return (
        <form onSubmit={handleSubmit} className="space-y-5">
            {error && (
                <div className="p-3 bg-red-50 border border-red-200 text-red-600 text-sm rounded-lg">
                    {error}
                </div>
            )}

            {/* Metric Selection (only for new conditions) */}
            {!condition && (
                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                        Metric
                    </label>
                    {availableMetrics.length > 0 ? (
                        <select
                            value={metricId}
                            onChange={(e) => setMetricId(parseInt(e.target.value))}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
                        >
                            <option value={0}>Select a metric...</option>
                            {availableMetrics.map((m) => (
                                <option key={m.id} value={m.id}>
                                    {m.label} ({m.unit})
                                </option>
                            ))}
                        </select>
                    ) : (
                        <p className="text-gray-500 text-sm">All metrics already have conditions.</p>
                    )}
                </div>
            )}

            {/* Show metric for editing */}
            {condition && (
                <div className="p-3 bg-gray-50 rounded-lg">
                    <span className="text-sm text-gray-500">Metric:</span>
                    <span className="ml-2 font-medium">{condition.metric.label}</span>
                    <span className="ml-1 text-gray-400">({condition.metric.unit})</span>
                </div>
            )}

            {/* Optimal Range */}
            <div className="grid grid-cols-2 gap-4">
                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                        Optimal Low {selectedMetric && `(${selectedMetric.unit})`}
                    </label>
                    <input
                        type="number"
                        step="any"
                        value={optimalLow}
                        onChange={(e) => setOptimalLow(e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                        placeholder="e.g., 15"
                    />
                </div>
                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                        Optimal High {selectedMetric && `(${selectedMetric.unit})`}
                    </label>
                    <input
                        type="number"
                        step="any"
                        value={optimalHigh}
                        onChange={(e) => setOptimalHigh(e.target.value)}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                        placeholder="e.g., 21"
                    />
                    {optimalLow && optimalHigh && parseFloat(optimalHigh) < parseFloat(optimalLow) && (
                        <p className="mt-1 text-xs text-red-500">Must be ≥ optimal low</p>
                    )}
                </div>
            </div>

            {/* Source Type (Radio) */}
            <div className="pt-2 border-t border-gray-100">
                <label className="block text-sm font-medium text-gray-700 mb-2">
                    Source
                </label>
                <div className="flex gap-4 mb-3">
                    <label className="flex items-center gap-2 cursor-pointer">
                        <input
                            type="radio"
                            name="sourceType"
                            checked={sourceType === 'url'}
                            onChange={() => setSourceType('url')}
                            className="w-4 h-4 text-blue-600"
                        />
                        <span className="text-sm">URL source</span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                        <input
                            type="radio"
                            name="sourceType"
                            checked={sourceType === 'own_experience'}
                            onChange={() => setSourceType('own_experience')}
                            className="w-4 h-4 text-blue-600"
                        />
                        <span className="text-sm">Own experience</span>
                    </label>
                </div>

                {/* URL Source Fields */}
                {sourceType === 'url' && (
                    <div className="space-y-3 pl-6 border-l-2 border-blue-100">
                        <div>
                            <label className="block text-xs font-medium text-gray-600 mb-1">
                                Source URL *
                            </label>
                            <input
                                type="url"
                                value={sourceUrl}
                                onChange={(e) => setSourceUrl(e.target.value)}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                                placeholder="https://extension.edu/plant-guide"
                                required={sourceType === 'url'}
                            />
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div>
                                <label className="block text-xs font-medium text-gray-600 mb-1">
                                    Title (optional)
                                </label>
                                <input
                                    type="text"
                                    value={sourceTitle}
                                    onChange={(e) => setSourceTitle(e.target.value)}
                                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                                    placeholder="Growing Guide"
                                />
                            </div>
                            <div>
                                <label className="block text-xs font-medium text-gray-600 mb-1">
                                    Publisher (optional)
                                </label>
                                <input
                                    type="text"
                                    value={sourcePublisher}
                                    onChange={(e) => setSourcePublisher(e.target.value)}
                                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                                    placeholder="Cornell Extension"
                                />
                            </div>
                        </div>
                    </div>
                )}

                {/* Own Experience Fields */}
                {sourceType === 'own_experience' && (
                    <div className="pl-6 border-l-2 border-green-100">
                        <label className="block text-xs font-medium text-gray-600 mb-1">
                            Notes (optional)
                        </label>
                        <input
                            type="text"
                            value={sourceNotes}
                            onChange={(e) => setSourceNotes(e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                            placeholder="e.g., Observed in greenhouse trials 2024"
                        />
                    </div>
                )}
            </div>

            {/* Actions */}
            <div className="flex justify-end gap-3 pt-4">
                <button
                    type="button"
                    onClick={onCancel}
                    className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
                >
                    Cancel
                </button>
                <button
                    type="submit"
                    disabled={!isFormValid || loading}
                    className="px-4 py-2 text-sm font-medium text-white bg-gray-800 rounded-lg hover:bg-gray-900 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors"
                >
                    {loading ? 'Saving...' : (condition ? 'Update' : 'Add Condition')}
                </button>
            </div>
        </form>
    );
}
