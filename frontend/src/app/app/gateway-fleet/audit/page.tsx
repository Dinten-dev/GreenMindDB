'use client';

import { useEffect, useState, useCallback } from 'react';
import { listGatewayAuditLogs } from '@/lib/gateway-admin-api';

export default function GatewayAuditPage() {
    const [logs, setLogs] = useState<Array<Record<string, unknown>>>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [actionFilter, setActionFilter] = useState('');
    const [page, setPage] = useState(0);
    const pageSize = 25;

    const loadLogs = useCallback(async () => {
        try {
            setLoading(true);
            const data = await listGatewayAuditLogs({
                action: actionFilter || undefined,
                offset: page * pageSize,
                limit: pageSize,
            });
            setLogs(data.items);
            setTotal(data.total);
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load audit logs');
        } finally {
            setLoading(false);
        }
    }, [actionFilter, page]);

    useEffect(() => { loadLogs(); }, [loadLogs]);

    const totalPages = Math.ceil(total / pageSize);

    const actionColors: Record<string, string> = {
        'gateway_release.upload': 'bg-blue-50 text-blue-600',
        'gateway_release.activate': 'bg-emerald-50 text-emerald-600',
        'gateway_release.deactivate': 'bg-amber-50 text-amber-600',
        'gateway_release.delete': 'bg-red-50 text-red-600',
        'gateway_config.upload': 'bg-indigo-50 text-indigo-600',
        'gateway_desired_state.update': 'bg-purple-50 text-purple-600',
        'gateway_command.issue': 'bg-cyan-50 text-cyan-600',
        'gateway_rollout.start': 'bg-emerald-50 text-emerald-700',
        'gateway_rollback.initiate': 'bg-orange-50 text-orange-600',
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-lg font-semibold text-gray-800">Audit Log</h2>
                    <p className="text-xs text-gray-400 mt-0.5">{total} Einträge</p>
                </div>
                <div className="flex items-center gap-3">
                    <select
                        value={actionFilter}
                        onChange={(e) => { setActionFilter(e.target.value); setPage(0); }}
                        className="px-3 py-2 rounded-xl border border-black/[0.08] bg-white text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                        id="audit-action-filter"
                    >
                        <option value="">Alle Actions</option>
                        <option value="gateway_release.upload">Release Upload</option>
                        <option value="gateway_release.activate">Release Activate</option>
                        <option value="gateway_release.delete">Release Delete</option>
                        <option value="gateway_config.upload">Config Upload</option>
                        <option value="gateway_desired_state.update">Desired State Update</option>
                        <option value="gateway_command.issue">Command Issue</option>
                        <option value="gateway_rollout.start">Rollout Start</option>
                        <option value="gateway_rollback.initiate">Rollback</option>
                    </select>
                    <button
                        onClick={loadLogs}
                        className="px-4 py-2 rounded-xl text-sm font-medium text-gray-600 hover:bg-black/[0.04] transition-colors"
                    >
                        ↻ Refresh
                    </button>
                </div>
            </div>

            {error && (
                <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-xl text-sm">
                    {error}
                </div>
            )}

            <div className="glass-card rounded-2xl overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="w-full text-sm" id="audit-logs-table">
                        <thead>
                            <tr className="border-b border-black/[0.06]">
                                {['Zeitpunkt', 'Action', 'Entity', 'Details', 'IP'].map((h) => (
                                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase tracking-wider">
                                        {h}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-black/[0.04]">
                            {loading ? (
                                <tr>
                                    <td colSpan={5} className="px-4 py-12 text-center text-gray-400">Lade Audit Logs…</td>
                                </tr>
                            ) : logs.length === 0 ? (
                                <tr>
                                    <td colSpan={5} className="px-4 py-12 text-center text-gray-400">Keine Audit Logs gefunden</td>
                                </tr>
                            ) : (
                                logs.map((log) => (
                                    <tr key={String(log.id)} className="hover:bg-black/[0.015] transition-colors">
                                        <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">
                                            {log.created_at
                                                ? new Date(String(log.created_at)).toLocaleString('de-CH', {
                                                      day: '2-digit',
                                                      month: '2-digit',
                                                      year: '2-digit',
                                                      hour: '2-digit',
                                                      minute: '2-digit',
                                                      second: '2-digit',
                                                  })
                                                : '—'}
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className={`px-2 py-0.5 rounded-md text-xs font-medium ${
                                                actionColors[String(log.action)] || 'bg-gray-50 text-gray-500'
                                            }`}>
                                                {String(log.action).replace('gateway_', '')}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3 text-xs text-gray-600">
                                            <span className="font-medium">{String(log.entity_type || '')}</span>
                                            {log.entity_id ? (
                                                <span className="text-gray-400 ml-1 font-mono">
                                                    {String(log.entity_id).substring(0, 8)}
                                                </span>
                                            ) : null}
                                        </td>
                                        <td className="px-4 py-3 text-xs text-gray-500 max-w-[300px] truncate font-mono">
                                            {String(log.details || '—')}
                                        </td>
                                        <td className="px-4 py-3 text-xs text-gray-400 font-mono">
                                            {String(log.ip_address || '—')}
                                        </td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                    </table>
                </div>

                {totalPages > 1 && (
                    <div className="flex items-center justify-between px-4 py-3 border-t border-black/[0.06]">
                        <button
                            onClick={() => setPage(Math.max(0, page - 1))}
                            disabled={page === 0}
                            className="px-3 py-1.5 rounded-lg text-xs text-gray-500 hover:bg-black/[0.04] disabled:opacity-30 transition-colors"
                        >
                            ← Zurück
                        </button>
                        <span className="text-xs text-gray-400">
                            Seite {page + 1} von {totalPages}
                        </span>
                        <button
                            onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
                            disabled={page >= totalPages - 1}
                            className="px-3 py-1.5 rounded-lg text-xs text-gray-500 hover:bg-black/[0.04] disabled:opacity-30 transition-colors"
                        >
                            Weiter →
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
