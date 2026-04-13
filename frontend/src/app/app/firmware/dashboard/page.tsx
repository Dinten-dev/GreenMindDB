'use client';

import { useState, useEffect } from 'react';
import { getDashboard, DashboardSummary, listReports, FirmwareReport } from '@/lib/firmware-api';

function StatCard({ label, value, sub, color }: { label: string; value: number; sub?: string; color: string }) {
    return (
        <div className="glass-card p-5">
            <p className="text-xs font-medium text-gray-400 uppercase tracking-wider">{label}</p>
            <p className={`text-3xl font-bold mt-1.5 ${color}`}>{value}</p>
            {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
        </div>
    );
}

export default function FirmwareDashboardPage() {
    const [data, setData] = useState<DashboardSummary | null>(null);
    const [recentFailed, setRecentFailed] = useState<FirmwareReport[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        (async () => {
            try {
                const [summary, reports] = await Promise.all([
                    getDashboard(),
                    listReports({ status: 'failed', limit: 5 }),
                ]);
                setData(summary);
                setRecentFailed(reports.items);
            } catch (err) {
                console.error('Dashboard load error:', err);
            } finally {
                setLoading(false);
            }
        })();
    }, []);

    if (loading || !data) {
        return (
            <div className="animate-pulse space-y-4">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    {[...Array(8)].map((_, i) => (
                        <div key={i} className="h-24 bg-black/[0.04] rounded-2xl" />
                    ))}
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            {/* Summary Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <StatCard label="Active Releases" value={data.active_releases} sub={`von ${data.total_releases} total`} color="text-emerald-600" />
                <StatCard label="Gateways Online" value={data.online_gateways} sub={`von ${data.total_gateways} total`} color="text-blue-600" />
                <StatCard label="Total Devices" value={data.total_devices} color="text-gray-800" />
                <StatCard label="Active Rollouts" value={data.active_rollouts} color="text-indigo-600" />
                <StatCard label="Updates (24h)" value={data.successful_updates_24h} sub="erfolgreich" color="text-emerald-600" />
                <StatCard label="Fehlgeschlagen (24h)" value={data.failed_updates_24h} color={data.failed_updates_24h > 0 ? 'text-red-600' : 'text-gray-400'} />
            </div>

            {/* Recent Failed */}
            {recentFailed.length > 0 && (
                <div className="glass-card p-6">
                    <h3 className="text-sm font-semibold text-gray-800 mb-4">Letzte fehlgeschlagene Updates</h3>
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="text-left text-xs text-gray-400 uppercase tracking-wider">
                                    <th className="pb-3 pr-4">Gateway</th>
                                    <th className="pb-3 pr-4">Version</th>
                                    <th className="pb-3 pr-4">Status</th>
                                    <th className="pb-3 pr-4">Fehler</th>
                                    <th className="pb-3">Zeit</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-black/[0.04]">
                                {recentFailed.map((r) => (
                                    <tr key={r.id}>
                                        <td className="py-2.5 pr-4 font-medium text-gray-700">{r.gateway_name || '–'}</td>
                                        <td className="py-2.5 pr-4 font-mono text-xs text-gray-600">{r.release_version || '–'}</td>
                                        <td className="py-2.5 pr-4">
                                            <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-red-50 text-red-600">
                                                {r.status}
                                            </span>
                                        </td>
                                        <td className="py-2.5 pr-4 text-xs text-gray-500 max-w-[200px] truncate">
                                            {r.error_message || '–'}
                                        </td>
                                        <td className="py-2.5 text-xs text-gray-400">
                                            {new Date(r.reported_at).toLocaleString('de-CH')}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
}
