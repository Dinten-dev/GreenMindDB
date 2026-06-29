'use client';

import { useState, useEffect, useCallback } from 'react';
import { listReports, FirmwareReportList } from '@/lib/firmware-api';

const STATUS_STYLES: Record<string, string> = {
    success: 'bg-emerald-50 text-emerald-600',
    failed: 'bg-red-50 text-red-600',
    hash_mismatch: 'bg-amber-50 text-amber-600',
    rollback: 'bg-purple-50 text-purple-600',
    incompatible: 'bg-orange-50 text-orange-600',
};

export default function ReportsPage() {
    const [data, setData] = useState<FirmwareReportList | null>(null);
    const [loading, setLoading] = useState(true);
    const [statusFilter, setStatusFilter] = useState('');
    const [page, setPage] = useState(0);
    const limit = 25;

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const res = await listReports({
                status: statusFilter || undefined,
                offset: page * limit,
                limit,
            });
            setData(res);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, [statusFilter, page]);

    useEffect(() => { load(); }, [load]);

    return (
        <div className="space-y-4">
            {/* Filters */}
            <div className="flex items-center gap-3">
                <select
                    value={statusFilter}
                    onChange={(e) => { setStatusFilter(e.target.value); setPage(0); }}
                    className="px-3 py-2 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30 backdrop-blur-sm"
                >
                    <option value="">Alle Status</option>
                    <option value="success">Erfolgreich</option>
                    <option value="failed">Fehlgeschlagen</option>
                    <option value="hash_mismatch">Hash Mismatch</option>
                    <option value="rollback">Rollback</option>
                    <option value="incompatible">Inkompatibel</option>
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
                                <th className="px-4 py-3">Gateway</th>
                                <th className="px-4 py-3">Version</th>
                                <th className="px-4 py-3">Status</th>
                                <th className="px-4 py-3">Fehler</th>
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
                                        Keine Update-Reports vorhanden.
                                    </td>
                                </tr>
                            ) : (
                                data?.items.map((r) => (
                                    <tr key={r.id} className="hover:bg-black/[0.01] transition-colors">
                                        <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">
                                            {new Date(r.reported_at).toLocaleString('de-CH')}
                                        </td>
                                        <td className="px-4 py-3 font-medium text-gray-700">
                                            {r.gateway_name || r.gateway_id.slice(0, 8)}
                                        </td>
                                        <td className="px-4 py-3 font-mono text-xs text-gray-600">
                                            {r.release_version || '–'}
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium ${
                                                STATUS_STYLES[r.status] || 'bg-gray-100 text-gray-500'
                                            }`}>
                                                {r.status}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3 text-xs text-gray-500 max-w-[250px] truncate" title={r.error_message || ''}>
                                            {r.error_message || '–'}
                                        </td>
                                    </tr>
                                ))
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
