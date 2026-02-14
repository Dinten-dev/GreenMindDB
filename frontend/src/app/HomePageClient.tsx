'use client';

import { useState, useEffect, useCallback } from 'react';
import {
    HealthStatus,
    DashboardOverview,
    Device,
    IngestLogEntry,
    fetchHealth,
    fetchOverview,
    fetchDevices,
    fetchIngestLog,
} from '@/lib/api';

// ── Helpers ──────────────────────────────────

function timeAgo(iso: string | null): string {
    if (!iso) return 'never';
    const diff = Date.now() - new Date(iso).getTime();
    const sec = Math.floor(diff / 1000);
    if (sec < 60) return `${sec}s ago`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hrs = Math.floor(min / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
}

function statusColor(status: string): string {
    switch (status.toLowerCase()) {
        case 'online': return 'bg-green-500';
        case 'offline': return 'bg-red-500';
        default: return 'bg-yellow-500';
    }
}

function deviceIcon(type: string): string {
    const t = type.toLowerCase();
    if (t.includes('esp32') || t.includes('esp')) return '📡';
    if (t.includes('raspberry') || t.includes('rpi') || t.includes('gateway')) return '🖥️';
    return '🔌';
}

// ── Main Dashboard ───────────────────────────

export default function HomePageClient() {
    const [health, setHealth] = useState<HealthStatus | null>(null);
    const [overview, setOverview] = useState<DashboardOverview | null>(null);
    const [devices, setDevices] = useState<Device[]>([]);
    const [ingestLog, setIngestLog] = useState<IngestLogEntry[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

    const refresh = useCallback(async () => {
        try {
            const [h, o, d, l] = await Promise.all([
                fetchHealth(),
                fetchOverview(),
                fetchDevices(),
                fetchIngestLog(30),
            ]);
            setHealth(h);
            setOverview(o);
            setDevices(d);
            setIngestLog(l);
            setError(null);
            setLastUpdated(new Date());
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Failed to load dashboard');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        refresh();
        const id = setInterval(refresh, 10_000);
        return () => clearInterval(id);
    }, [refresh]);

    // ── Loading ──────────────────────────────

    if (loading) {
        return (
            <div className="max-w-7xl mx-auto px-6 py-12">
                <div className="animate-pulse space-y-6">
                    <div className="h-8 w-64 bg-gray-200 rounded-lg" />
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        {[1, 2, 3, 4].map(i => (
                            <div key={i} className="h-28 bg-gray-200 rounded-2xl" />
                        ))}
                    </div>
                    <div className="h-64 bg-gray-200 rounded-2xl" />
                </div>
            </div>
        );
    }

    // ── Error ────────────────────────────────

    if (error && !overview) {
        return (
            <div className="max-w-7xl mx-auto px-6 py-12">
                <div className="bg-red-50 border border-red-200 text-red-700 rounded-2xl p-8 text-center">
                    <p className="text-lg font-medium mb-2">Connection Error</p>
                    <p className="text-sm text-red-500">{error}</p>
                    <button
                        onClick={refresh}
                        className="mt-4 px-4 py-2 bg-red-100 hover:bg-red-200 text-red-700 rounded-lg text-sm transition-colors"
                    >
                        Retry
                    </button>
                </div>
            </div>
        );
    }

    return (
        <div className="max-w-7xl mx-auto px-6 py-8">
            {/* ── Title ─────────────────────────── */}
            <div className="flex items-center justify-between mb-8">
                <div>
                    <h1 className="text-3xl font-semibold text-gray-800">Dashboard</h1>
                    <p className="text-gray-400 text-sm mt-1">
                        Data ingestion pipeline overview
                    </p>
                </div>
                <div className="text-xs text-gray-400">
                    {lastUpdated && <>Updated {timeAgo(lastUpdated.toISOString())} · </>}
                    Auto-refresh 10s
                </div>
            </div>

            {/* ── Health Bar ────────────────────── */}
            {health && (
                <div className={`rounded-2xl p-4 mb-6 flex items-center gap-6 text-sm font-medium ${health.status === 'ok'
                        ? 'bg-green-50 border border-green-200 text-green-800'
                        : 'bg-red-50 border border-red-200 text-red-800'
                    }`}>
                    <div className="flex items-center gap-2">
                        <span className={`w-3 h-3 rounded-full ${health.db === 'ok' ? 'bg-green-500' : 'bg-red-500'}`} />
                        PostgreSQL + TimescaleDB
                    </div>
                    <div className="flex items-center gap-2">
                        <span className={`w-3 h-3 rounded-full ${health.minio === 'ok' ? 'bg-green-500' : 'bg-red-500'}`} />
                        MinIO (S3)
                    </div>
                </div>
            )}

            {/* ── Overview Stats ─────────────────── */}
            {overview && (
                <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-4 mb-8">
                    <StatCard label="Greenhouses" value={overview.greenhouses} icon="🏠" />
                    <StatCard label="Devices" value={overview.devices} icon="📡" />
                    <StatCard label="Sensors" value={overview.sensors} icon="🌡️" />
                    <StatCard label="Plants" value={overview.plants} icon="🌱" />
                    <StatCard label="Signals (24h)" value={overview.signal_rows_24h} icon="⚡" accent />
                    <StatCard label="Env rows (24h)" value={overview.env_rows_24h} icon="🌤️" accent />
                    <StatCard label="Ingests (24h)" value={overview.ingests_24h} icon="📥" accent />
                </div>
            )}

            {/* ── Devices Section ─────────────────── */}
            <section className="mb-8">
                <h2 className="text-xl font-semibold text-gray-800 mb-4">
                    Devices
                    <span className="text-gray-400 font-normal text-sm ml-2">
                        ESP32 Nodes & Raspberry Pi Gateways
                    </span>
                </h2>

                {devices.length === 0 ? (
                    <div className="bg-white border border-gray-200 rounded-2xl p-8 text-center text-gray-400">
                        <p className="text-4xl mb-3">📡</p>
                        <p className="font-medium text-gray-500">No devices registered</p>
                        <p className="text-sm mt-1">Devices will appear here once they send data via the ingestion API</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {devices.map(dev => (
                            <div
                                key={dev.id}
                                className="bg-white border border-gray-200 rounded-2xl p-5 hover:shadow-lg transition-shadow"
                            >
                                <div className="flex items-start justify-between mb-3">
                                    <div className="flex items-center gap-2">
                                        <span className="text-2xl">{deviceIcon(dev.type)}</span>
                                        <div>
                                            <p className="font-semibold text-gray-800">{dev.serial}</p>
                                            <p className="text-xs text-gray-400">{dev.type}</p>
                                        </div>
                                    </div>
                                    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium text-white ${statusColor(dev.status)}`}>
                                        {dev.status}
                                    </span>
                                </div>

                                <div className="grid grid-cols-2 gap-y-2 text-sm">
                                    <div className="text-gray-400">Firmware</div>
                                    <div className="text-gray-700 font-mono text-xs">{dev.fw_version || '—'}</div>

                                    <div className="text-gray-400">Sensors</div>
                                    <div className="text-gray-700">{dev.sensor_count}</div>

                                    <div className="text-gray-400">Last Seen</div>
                                    <div className="text-gray-700">{timeAgo(dev.last_seen)}</div>

                                    {dev.greenhouse_name && (
                                        <>
                                            <div className="text-gray-400">Greenhouse</div>
                                            <div className="text-gray-700">{dev.greenhouse_name}</div>
                                        </>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </section>

            {/* ── Ingestion Activity Feed ──────────── */}
            <section>
                <h2 className="text-xl font-semibold text-gray-800 mb-4">
                    Ingestion Activity
                    <span className="text-gray-400 font-normal text-sm ml-2">
                        Recent data ingestion events
                    </span>
                </h2>

                {ingestLog.length === 0 ? (
                    <div className="bg-white border border-gray-200 rounded-2xl p-8 text-center text-gray-400">
                        <p className="text-4xl mb-3">📥</p>
                        <p className="font-medium text-gray-500">No ingestion events yet</p>
                        <p className="text-sm mt-1">Events will appear when data is sent to the API via POST /v1/ingest/*</p>
                    </div>
                ) : (
                    <div className="bg-white border border-gray-200 rounded-2xl overflow-hidden">
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="bg-gray-50 border-b border-gray-200 text-left text-gray-500">
                                        <th className="px-4 py-3 font-medium">Status</th>
                                        <th className="px-4 py-3 font-medium">Endpoint</th>
                                        <th className="px-4 py-3 font-medium">Source</th>
                                        <th className="px-4 py-3 font-medium">Request ID</th>
                                        <th className="px-4 py-3 font-medium">Time</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-100">
                                    {ingestLog.map(entry => (
                                        <tr key={entry.request_id} className="hover:bg-gray-50 transition-colors">
                                            <td className="px-4 py-3">
                                                <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${entry.status === 'ingested'
                                                        ? 'bg-green-100 text-green-700'
                                                        : entry.status === 'duplicate'
                                                            ? 'bg-yellow-100 text-yellow-700'
                                                            : 'bg-red-100 text-red-700'
                                                    }`}>
                                                    {entry.status}
                                                </span>
                                            </td>
                                            <td className="px-4 py-3 font-mono text-xs text-gray-600">{entry.endpoint}</td>
                                            <td className="px-4 py-3 text-gray-600">{entry.source || '—'}</td>
                                            <td className="px-4 py-3 font-mono text-xs text-gray-400">{entry.request_id.slice(0, 8)}…</td>
                                            <td className="px-4 py-3 text-gray-400">{timeAgo(entry.received_at)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}
            </section>
        </div>
    );
}

// ── Stat Card Component ──────────────────────

function StatCard({ label, value, icon, accent }: { label: string; value: number; icon: string; accent?: boolean }) {
    return (
        <div className={`rounded-2xl p-4 border ${accent
                ? 'bg-gradient-to-br from-blue-50 to-indigo-50 border-blue-200'
                : 'bg-white border-gray-200'
            }`}>
            <div className="flex items-center gap-2 mb-2">
                <span className="text-lg">{icon}</span>
                <span className="text-xs text-gray-400 uppercase tracking-wide">{label}</span>
            </div>
            <p className={`text-2xl font-bold ${accent ? 'text-blue-700' : 'text-gray-800'}`}>
                {value.toLocaleString()}
            </p>
        </div>
    );
}
