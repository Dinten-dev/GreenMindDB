'use client';

import { useEffect, useState, useCallback } from 'react';
import { listUpdateLogs, type GatewayUpdateLog } from '@/lib/gateway-admin-api';

function StatusBadge({ status }: { status: string }) {
    const styles: Record<string, string> = {
        apply_success: 'bg-emerald-50 text-emerald-700 border-emerald-200',
        apply_failed: 'bg-red-50 text-red-600 border-red-200',
        download_started: 'bg-blue-50 text-blue-600 border-blue-200',
        download_complete: 'bg-indigo-50 text-indigo-600 border-indigo-200',
        healthcheck_failed: 'bg-amber-50 text-amber-600 border-amber-200',
        rollback_success: 'bg-purple-50 text-purple-600 border-purple-200',
        rollback_failed: 'bg-red-50 text-red-700 border-red-200',
    };

    return (
        <span className={`inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-medium border ${
            styles[status] || 'bg-gray-50 text-gray-500 border-gray-200'
        }`}>
            {status.replace(/_/g, ' ')}
        </span>
    );
}

export default function GatewayLogsPage() {
    const [logs, setLogs] = useState<GatewayUpdateLog[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [typeFilter, setTypeFilter] = useState<string>('');
    const [page, setPage] = useState(0);
    const pageSize = 25;

    const loadLogs = useCallback(async () => {
        try {
            setLoading(true);
            const data = await listUpdateLogs({
                update_type: typeFilter || undefined,
                offset: page * pageSize,
                limit: pageSize,
            });
            setLogs(data.items);
            setTotal(data.total);
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load logs');
        } finally {
            setLoading(false);
        }
    }, [typeFilter, page]);

    useEffect(() => { loadLogs(); }, [loadLogs]);

    const totalPages = Math.ceil(total / pageSize);

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-lg font-semibold text-gray-800">Update Logs</h2>
                    <p className="text-xs text-gray-400 mt-0.5">{total} Einträge</p>
                </div>
                <div className="flex items-center gap-3">
                    <select
                        value={typeFilter}
                        onChange={(e) => { setTypeFilter(e.target.value); setPage(0); }}
                        className="px-3 py-2 rounded-xl border border-black/[0.08] bg-white text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                        id="log-type-filter"
                    >
                        <option value="">Alle Typen</option>
                        <option value="app">App Updates</option>
                        <option value="config">Config Updates</option>
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
                    <table className="w-full text-sm" id="update-logs-table">
                        <thead>
                            <tr className="border-b border-black/[0.06]">
                                {['Gateway', 'Typ', 'Von', 'Nach', 'Status', 'Fehler', 'Gestartet', 'Beendet'].map((h) => (
                                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase tracking-wider">
                                        {h}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-black/[0.04]">
                            {loading ? (
                                <tr>
                                    <td colSpan={8} className="px-4 py-12 text-center text-gray-400">Lade Logs…</td>
                                </tr>
                            ) : logs.length === 0 ? (
                                <tr>
                                    <td colSpan={8} className="px-4 py-12 text-center text-gray-400">Keine Update Logs gefunden</td>
                                </tr>
                            ) : (
                                logs.map((log) => (
                                    <tr key={log.id} className="hover:bg-black/[0.015] transition-colors">
                                        <td className="px-4 py-3 font-medium text-gray-800">
                                            {log.gateway_name || log.gateway_id.substring(0, 8)}
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className={`px-2 py-0.5 rounded-md text-xs font-medium ${
                                                log.update_type === 'app' ? 'bg-blue-50 text-blue-600' : 'bg-purple-50 text-purple-600'
                                            }`}>
                                                {log.update_type}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3 font-mono text-xs text-gray-500">
                                            {log.from_version || '—'}
                                        </td>
                                        <td className="px-4 py-3 font-mono text-xs text-gray-800">
                                            {log.to_version}
                                        </td>
                                        <td className="px-4 py-3">
                                            <StatusBadge status={log.status} />
                                        </td>
                                        <td className="px-4 py-3 text-xs text-red-500 max-w-[200px] truncate">
                                            {log.error_message || '—'}
                                        </td>
                                        <td className="px-4 py-3 text-xs text-gray-400">
                                            {new Date(log.started_at).toLocaleString('de-CH', {
                                                day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
                                            })}
                                        </td>
                                        <td className="px-4 py-3 text-xs text-gray-400">
                                            {log.completed_at
                                                ? new Date(log.completed_at).toLocaleString('de-CH', {
                                                      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
                                                  })
                                                : '—'}
                                        </td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                    </table>
                </div>

                {/* Pagination */}
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
