'use client';

import { useState, useEffect, useCallback } from 'react';
import { listAuditLogs, AuditLogList } from '@/lib/firmware-api';

const ACTION_LABELS: Record<string, { label: string; color: string }> = {
    'firmware.upload': { label: 'Upload', color: 'bg-blue-50 text-blue-600' },
    'firmware.activate': { label: 'Aktiviert', color: 'bg-emerald-50 text-emerald-600' },
    'firmware.deactivate': { label: 'Deaktiviert', color: 'bg-amber-50 text-amber-600' },
    'firmware.delete': { label: 'Gelöscht', color: 'bg-red-50 text-red-600' },
    'rollout.create': { label: 'Rollout erstellt', color: 'bg-indigo-50 text-indigo-600' },
    'rollout.delete': { label: 'Rollout entfernt', color: 'bg-orange-50 text-orange-600' },
};

export default function AuditPage() {
    const [data, setData] = useState<AuditLogList | null>(null);
    const [loading, setLoading] = useState(true);
    const [actionFilter, setActionFilter] = useState('');
    const [page, setPage] = useState(0);
    const limit = 25;

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const res = await listAuditLogs({
                action: actionFilter || undefined,
                offset: page * limit,
                limit,
            });
            setData(res);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, [actionFilter, page]);

    useEffect(() => { load(); }, [load]);

    const parseDetails = (details: string | null): Record<string, string> => {
        if (!details) return {};
        try { return JSON.parse(details); } catch { return {}; }
    };

    return (
        <div className="space-y-4">
            {/* Filters */}
            <div className="flex items-center gap-3">
                <select
                    value={actionFilter}
                    onChange={(e) => { setActionFilter(e.target.value); setPage(0); }}
                    className="px-3 py-2 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30 backdrop-blur-sm"
                >
                    <option value="">Alle Aktionen</option>
                    {Object.entries(ACTION_LABELS).map(([key, { label }]) => (
                        <option key={key} value={key}>{label}</option>
                    ))}
                </select>
                {data && (
                    <span className="text-xs text-gray-400">{data.meta.total} Einträge</span>
                )}
            </div>

            {/* Table */}
            <div className="glass-card overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="text-left text-xs text-gray-400 uppercase tracking-wider bg-black/[0.02]">
                                <th className="px-4 py-3">Zeitpunkt</th>
                                <th className="px-4 py-3">Benutzer</th>
                                <th className="px-4 py-3">Aktion</th>
                                <th className="px-4 py-3">Details</th>
                                <th className="px-4 py-3">IP</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-black/[0.04]">
                            {loading ? (
                                [...Array(5)].map((_, i) => (
                                    <tr key={i}>
                                        <td colSpan={5} className="px-4 py-4">
                                            <div className="h-4 bg-black/[0.04] rounded w-full animate-pulse" />
                                        </td>
                                    </tr>
                                ))
                            ) : data?.items.length === 0 ? (
                                <tr>
                                    <td colSpan={5} className="px-4 py-12 text-center text-gray-400">
                                        Keine Audit-Einträge vorhanden.
                                    </td>
                                </tr>
                            ) : (
                                data?.items.map((entry) => {
                                    const actionStyle = ACTION_LABELS[entry.action] || { label: entry.action, color: 'bg-gray-100 text-gray-500' };
                                    const details = parseDetails(entry.details);
                                    return (
                                        <tr key={entry.id} className="hover:bg-black/[0.01] transition-colors">
                                            <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">
                                                {new Date(entry.created_at).toLocaleString('de-CH')}
                                            </td>
                                            <td className="px-4 py-3 text-gray-700">
                                                {entry.user_email || '–'}
                                            </td>
                                            <td className="px-4 py-3">
                                                <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium ${actionStyle.color}`}>
                                                    {actionStyle.label}
                                                </span>
                                            </td>
                                            <td className="px-4 py-3">
                                                <div className="flex flex-wrap gap-1.5">
                                                    {Object.entries(details).map(([k, v]) => (
                                                        <span key={k} className="text-xs bg-black/[0.03] rounded px-1.5 py-0.5 text-gray-600" title={`${k}: ${v}`}>
                                                            <span className="text-gray-400">{k}:</span> {String(v).slice(0, 20)}
                                                        </span>
                                                    ))}
                                                </div>
                                            </td>
                                            <td className="px-4 py-3 text-xs text-gray-400 font-mono">
                                                {entry.ip_address || '–'}
                                            </td>
                                        </tr>
                                    );
                                })
                            )}
                        </tbody>
                    </table>
                </div>

                {/* Pagination */}
                {data && data.meta.total > limit && (
                    <div className="flex items-center justify-between px-4 py-3 border-t border-black/[0.04]">
                        <button
                            disabled={page === 0}
                            onClick={() => setPage(p => Math.max(0, p - 1))}
                            className="px-3 py-1.5 rounded-lg text-xs font-medium text-gray-600 hover:bg-black/[0.04] disabled:opacity-30 transition-colors"
                        >
                            ← Zurück
                        </button>
                        <span className="text-xs text-gray-400">
                            Seite {page + 1} von {Math.ceil(data.meta.total / limit)}
                        </span>
                        <button
                            disabled={(page + 1) * limit >= data.meta.total}
                            onClick={() => setPage(p => p + 1)}
                            className="px-3 py-1.5 rounded-lg text-xs font-medium text-gray-600 hover:bg-black/[0.04] disabled:opacity-30 transition-colors"
                        >
                            Weiter →
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
