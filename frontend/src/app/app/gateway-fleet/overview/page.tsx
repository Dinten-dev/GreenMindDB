'use client';

import { useEffect, useState, useCallback } from 'react';
import {
    getFleetOverview,
    issueCommand,
    toggleMaintenance,
    toggleBlock,
    type GatewayFleetItem,
} from '@/lib/gateway-admin-api';

function StatusBadge({ status }: { status: string }) {
    const styles: Record<string, string> = {
        online: 'bg-emerald-50 text-emerald-700 border-emerald-200',
        offline: 'bg-gray-50 text-gray-500 border-gray-200',
    };
    return (
        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border ${styles[status] || styles.offline}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${status === 'online' ? 'bg-emerald-500' : 'bg-gray-400'}`} />
            {status}
        </span>
    );
}

function DiskBadge({ status }: { status: string | null }) {
    if (!status) return <span className="text-xs text-gray-300">—</span>;
    const styles: Record<string, string> = {
        ok: 'text-emerald-600 bg-emerald-50',
        low: 'text-amber-600 bg-amber-50',
        critical: 'text-red-600 bg-red-50',
    };
    return (
        <span className={`px-2 py-0.5 rounded-md text-xs font-medium ${styles[status] || ''}`}>
            {status}
        </span>
    );
}

function SignatureBadge({ status }: { status: string | null }) {
    if (!status) return <span className="text-xs text-gray-300">—</span>;
    const map: Record<string, { icon: string; cls: string }> = {
        signed: { icon: '✓', cls: 'text-emerald-600' },
        unsigned: { icon: '⚠', cls: 'text-amber-500' },
        invalid: { icon: '✕', cls: 'text-red-600' },
    };
    const s = map[status] || map.unsigned;
    return <span className={`text-xs font-medium ${s.cls}`}>{s.icon} {status}</span>;
}

function UpdateStatusBadge({ download, apply }: { download: string | null; apply: string | null }) {
    const effective = apply && apply !== 'none' ? apply : download && download !== 'none' ? download : null;
    if (!effective) return <span className="text-xs text-gray-300">—</span>;

    const styles: Record<string, string> = {
        downloading: 'text-blue-600 bg-blue-50',
        downloaded: 'text-indigo-600 bg-indigo-50',
        pending_window: 'text-amber-600 bg-amber-50',
        applying: 'text-purple-600 bg-purple-50',
        applied: 'text-emerald-600 bg-emerald-50',
        failed: 'text-red-600 bg-red-50',
        disk_insufficient: 'text-red-600 bg-red-50',
    };

    return (
        <span className={`px-2 py-0.5 rounded-md text-xs font-medium ${styles[effective] || 'text-gray-500 bg-gray-50'}`}>
            {effective.replace(/_/g, ' ')}
        </span>
    );
}

export default function FleetOverviewPage() {
    const [fleet, setFleet] = useState<GatewayFleetItem[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [statusFilter, setStatusFilter] = useState<string>('');
    const [actionGw, setActionGw] = useState<string | null>(null);

    const loadFleet = useCallback(async () => {
        try {
            setLoading(true);
            const data = await getFleetOverview({
                status: statusFilter || undefined,
                limit: 100,
            });
            setFleet(data.items);
            setTotal(data.total);
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load fleet');
        } finally {
            setLoading(false);
        }
    }, [statusFilter]);

    useEffect(() => { loadFleet(); }, [loadFleet]);

    const handleCommand = async (gatewayId: string, cmd: string) => {
        try {
            await issueCommand(gatewayId, cmd);
            setActionGw(null);
            loadFleet();
        } catch (err) {
            alert(err instanceof Error ? err.message : 'Command failed');
        }
    };

    const handleMaintenance = async (gatewayId: string, enabled: boolean) => {
        try {
            await toggleMaintenance(gatewayId, enabled);
            loadFleet();
        } catch (err) {
            alert(err instanceof Error ? err.message : 'Failed');
        }
    };

    const handleBlock = async (gatewayId: string, blocked: boolean) => {
        try {
            await toggleBlock(gatewayId, blocked);
            loadFleet();
        } catch (err) {
            alert(err instanceof Error ? err.message : 'Failed');
        }
    };

    const onlineCount = fleet.filter((g) => g.status === 'online').length;
    const offlineCount = fleet.filter((g) => g.status === 'offline').length;

    return (
        <div className="space-y-6">
            {/* Stats */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                {[
                    { label: 'Total', value: total, color: 'text-gray-800' },
                    { label: 'Online', value: onlineCount, color: 'text-emerald-600' },
                    { label: 'Offline', value: offlineCount, color: 'text-gray-400' },
                    { label: 'Maintenance', value: fleet.filter((g) => g.maintenance_mode).length, color: 'text-amber-500' },
                ].map((stat) => (
                    <div key={stat.label} className="glass-card rounded-2xl p-4">
                        <p className="text-xs text-gray-400 uppercase tracking-wider">{stat.label}</p>
                        <p className={`text-2xl font-bold mt-1 ${stat.color}`}>{stat.value}</p>
                    </div>
                ))}
            </div>

            {/* Filters */}
            <div className="flex items-center gap-3">
                <select
                    id="fleet-status-filter"
                    value={statusFilter}
                    onChange={(e) => setStatusFilter(e.target.value)}
                    className="px-3 py-2 rounded-xl border border-black/[0.08] bg-white text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                >
                    <option value="">Alle Status</option>
                    <option value="online">Online</option>
                    <option value="offline">Offline</option>
                </select>
                <button
                    onClick={loadFleet}
                    className="px-4 py-2 rounded-xl text-sm font-medium text-gray-600 hover:bg-black/[0.04] transition-colors"
                >
                    ↻ Refresh
                </button>
            </div>

            {/* Error */}
            {error && (
                <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-xl text-sm">
                    {error}
                </div>
            )}

            {/* Table */}
            <div className="glass-card rounded-2xl overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="w-full text-sm" id="fleet-table">
                        <thead>
                            <tr className="border-b border-black/[0.06]">
                                {['Gateway', 'Status', 'App', 'Config', 'Ring', 'Update', 'Disk', 'Sig', 'Window', 'Last Seen', 'Actions'].map(
                                    (h) => (
                                        <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase tracking-wider">
                                            {h}
                                        </th>
                                    )
                                )}
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-black/[0.04]">
                            {loading ? (
                                <tr>
                                    <td colSpan={11} className="px-4 py-12 text-center text-gray-400">
                                        Lade Fleet-Daten…
                                    </td>
                                </tr>
                            ) : fleet.length === 0 ? (
                                <tr>
                                    <td colSpan={11} className="px-4 py-12 text-center text-gray-400">
                                        Keine Gateways gefunden
                                    </td>
                                </tr>
                            ) : (
                                fleet.map((gw) => (
                                    <tr key={gw.id} className="hover:bg-black/[0.015] transition-colors">
                                        <td className="px-4 py-3">
                                            <p className="font-medium text-gray-800">{gw.name || gw.hardware_id}</p>
                                            <p className="text-xs text-gray-400">{gw.zone_name || '—'}</p>
                                        </td>
                                        <td className="px-4 py-3">
                                            <StatusBadge status={gw.status} />
                                            {gw.maintenance_mode && (
                                                <span className="ml-1 text-xs text-amber-500">⚙ maint</span>
                                            )}
                                            {gw.blocked && (
                                                <span className="ml-1 text-xs text-red-500">⊘ blocked</span>
                                            )}
                                        </td>
                                        <td className="px-4 py-3 text-xs">
                                            <span className="font-mono">{gw.app_version || '—'}</span>
                                            {gw.desired_app_version && gw.desired_app_version !== gw.app_version && (
                                                <span className="block text-amber-500 mt-0.5">→ {gw.desired_app_version}</span>
                                            )}
                                        </td>
                                        <td className="px-4 py-3 text-xs font-mono">
                                            {gw.config_version || '—'}
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className="px-2 py-0.5 rounded-md text-xs font-medium bg-gray-50 text-gray-600">
                                                {gw.rollout_ring || 'stable'}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3">
                                            <UpdateStatusBadge
                                                download={gw.update_download_status}
                                                apply={gw.update_apply_status}
                                            />
                                        </td>
                                        <td className="px-4 py-3">
                                            <DiskBadge status={gw.disk_status} />
                                            {gw.disk_free_mb !== null && (
                                                <span className="block text-[10px] text-gray-400 mt-0.5">
                                                    {gw.disk_free_mb} MB
                                                </span>
                                            )}
                                        </td>
                                        <td className="px-4 py-3">
                                            <SignatureBadge status={gw.signature_status} />
                                        </td>
                                        <td className="px-4 py-3 text-xs text-gray-500">
                                            {gw.update_window || 'anytime'}
                                        </td>
                                        <td className="px-4 py-3 text-xs text-gray-400">
                                            {gw.last_seen
                                                ? new Date(gw.last_seen).toLocaleString('de-CH', {
                                                      hour: '2-digit',
                                                      minute: '2-digit',
                                                      day: '2-digit',
                                                      month: '2-digit',
                                                  })
                                                : '—'}
                                        </td>
                                        <td className="px-4 py-3 relative">
                                            <button
                                                onClick={() => setActionGw(actionGw === gw.id ? null : gw.id)}
                                                className="px-2 py-1 rounded-lg text-xs text-gray-500 hover:bg-black/[0.05] transition-colors"
                                                id={`gw-action-${gw.id}`}
                                            >
                                                ⋯
                                            </button>
                                            {actionGw === gw.id && (
                                                <div className="absolute right-4 top-10 z-20 w-52 bg-white rounded-xl shadow-lg border border-black/[0.08] py-1.5">
                                                    <button
                                                        onClick={() => handleCommand(gw.id, 'restart_gateway_service')}
                                                        className="w-full px-4 py-2 text-left text-xs hover:bg-black/[0.03] transition-colors"
                                                    >
                                                        🔄 Restart Service
                                                    </button>
                                                    <button
                                                        onClick={() => handleCommand(gw.id, 'reload_gateway_config')}
                                                        className="w-full px-4 py-2 text-left text-xs hover:bg-black/[0.03] transition-colors"
                                                    >
                                                        ⚙ Reload Config
                                                    </button>
                                                    <button
                                                        onClick={() => handleMaintenance(gw.id, !gw.maintenance_mode)}
                                                        className="w-full px-4 py-2 text-left text-xs hover:bg-black/[0.03] transition-colors"
                                                    >
                                                        {gw.maintenance_mode ? '▶ Exit Maintenance' : '⏸ Enter Maintenance'}
                                                    </button>
                                                    <button
                                                        onClick={() => handleBlock(gw.id, !gw.blocked)}
                                                        className="w-full px-4 py-2 text-left text-xs hover:bg-black/[0.03] transition-colors"
                                                    >
                                                        {gw.blocked ? '🔓 Unblock' : '🔒 Block Updates'}
                                                    </button>
                                                    <hr className="my-1 border-black/[0.06]" />
                                                    <button
                                                        onClick={() => handleCommand(gw.id, 'controlled_reboot')}
                                                        className="w-full px-4 py-2 text-left text-xs text-red-500 hover:bg-red-50/60 transition-colors"
                                                    >
                                                        ⚡ Reboot
                                                    </button>
                                                </div>
                                            )}
                                        </td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}
