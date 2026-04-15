'use client';

import { useState, useEffect, useCallback } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import {
    apiListSensors, apiGetSensorDataAdvanced, apiExportSensorData,
    SensorInfo, SensorDataResponse,
} from '@/lib/api';
import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';

type Resolution = 'raw' | '1m' | '5m' | '1h' | '1d';

const KIND_CONFIG: Record<string, { label: string; unit: string; color: string; icon: string }> = {
    temperature: { label: 'Temperatur', unit: '°C', color: '#ef4444', icon: '🌡' },
    humidity: { label: 'Luftfeuchtigkeit', unit: '%', color: '#3b82f6', icon: '💧' },
    soil_moisture: { label: 'Bodenfeuchtigkeit', unit: '%', color: '#a855f7', icon: '🌱' },
    bioelectric: { label: 'Bioelektrisches Signal', unit: 'mV', color: '#10b981', icon: '⚡' },
    bio_signal: { label: 'Bioelektrisches Signal', unit: 'mV', color: '#10b981', icon: '⚡' },
};

function formatDate(d: Date): string {
    return d.toISOString().split('T')[0];
}

function friendlyDate(iso: string): string {
    const d = new Date(iso + 'T00:00:00');
    return d.toLocaleDateString('de-CH', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
}

type ExportStatus = 'idle' | 'loading' | 'done' | 'error';

export default function SensorDetailPage() {
    const { id: zoneId, sensorId } = useParams<{ id: string; sensorId: string }>();

    const [sensor, setSensor] = useState<SensorInfo | null>(null);
    const [sensorData, setSensorData] = useState<SensorDataResponse[]>([]);
    const [loading, setLoading] = useState(true);
    const [loadingData, setLoadingData] = useState(false);

    const [selectedDate, setSelectedDate] = useState(formatDate(new Date()));
    const [resolution, setResolution] = useState<Resolution>('5m');
    const [exportStatus, setExportStatus] = useState<ExportStatus>('idle');

    // Load sensor info
    useEffect(() => {
        apiListSensors(zoneId)
            .then(sensors => {
                const found = sensors.find(s => s.id === sensorId);
                setSensor(found || null);
            })
            .catch(console.error)
            .finally(() => setLoading(false));
    }, [zoneId, sensorId]);

    // Load sensor data
    const loadData = useCallback(async () => {
        setLoadingData(true);
        try {
            const data = await apiGetSensorDataAdvanced(sensorId, {
                date: selectedDate,
                resolution,
            });
            setSensorData(data);
        } catch (err) {
            console.error('Failed to load sensor data:', err);
            setSensorData([]);
        } finally {
            setLoadingData(false);
        }
    }, [sensorId, selectedDate, resolution]);

    useEffect(() => {
        if (sensorId) loadData();
    }, [sensorId, loadData]);

    const shiftDay = (delta: number) => {
        const d = new Date(selectedDate + 'T00:00:00');
        d.setDate(d.getDate() + delta);
        setSelectedDate(formatDate(d));
    };

    const handleExport = async () => {
        setExportStatus('loading');
        try {
            await apiExportSensorData(sensorId, '24h');
            setExportStatus('done');
            setTimeout(() => setExportStatus('idle'), 2000);
        } catch {
            setExportStatus('error');
            setTimeout(() => setExportStatus('idle'), 3000);
        }
    };

    if (loading) {
        return (
            <div className="animate-pulse space-y-4">
                <div className="h-6 w-48 bg-black/[0.04] rounded-xl" />
                <div className="h-64 bg-black/[0.04] rounded-2xl" />
            </div>
        );
    }

    if (!sensor) {
        return (
            <div className="glass-card p-12 text-center">
                <h3 className="text-lg font-semibold text-gray-800">Sensor nicht gefunden</h3>
            </div>
        );
    }

    const isToday = selectedDate === formatDate(new Date());

    return (
        <div className="space-y-6">
            {/* Breadcrumb */}
            <div className="flex items-center gap-2 text-sm text-gray-400 flex-wrap">
                <Link href="/app/zones" className="hover:text-gray-600 transition-colors">Zonen</Link>
                <span>›</span>
                <Link href={`/app/zones/${zoneId}`} className="hover:text-gray-600 transition-colors">Detail</Link>
                <span>›</span>
                <span className="text-gray-700 font-medium">{sensor.name || sensor.mac_address}</span>
            </div>

            {/* Sensor Header */}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-gray-800 tracking-tight">
                        {sensor.name || sensor.mac_address}
                    </h1>
                    <p className="text-sm text-gray-400 mt-1 flex items-center gap-2">
                        <span className="font-mono">{sensor.mac_address}</span>
                        <span>·</span>
                        <span>{sensor.gateway_name}</span>
                        <span>·</span>
                        <span className={`inline-flex items-center gap-1 ${sensor.status === 'online' ? 'text-emerald-600' : 'text-gray-400'}`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${sensor.status === 'online' ? 'bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.4)]' : 'bg-gray-300'}`} />
                            {sensor.status}
                        </span>
                    </p>
                </div>

                {/* Export */}
                <button
                    onClick={handleExport}
                    disabled={exportStatus !== 'idle'}
                    className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-xl bg-emerald-50 text-emerald-700 hover:bg-emerald-100 transition-all disabled:opacity-50 border border-emerald-200/50 shrink-0"
                >
                    {exportStatus === 'idle' && (
                        <>
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                <polyline points="7 10 12 15 17 10" />
                                <line x1="12" y1="15" x2="12" y2="3" />
                            </svg>
                            Forschungsdaten-Sicherung
                        </>
                    )}
                    {exportStatus === 'loading' && 'Exportiere…'}
                    {exportStatus === 'done' && '✓ Fertig'}
                    {exportStatus === 'error' && '✗ Fehler'}
                </button>
            </div>

            {/* Controls Bar */}
            <div className="glass-card p-4">
                <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4">
                    {/* Date Navigation */}
                    <div className="flex items-center gap-2">
                        <button
                            onClick={() => shiftDay(-1)}
                            className="p-2 rounded-xl bg-black/[0.03] hover:bg-black/[0.06] text-gray-500 transition-colors"
                            title="Vorheriger Tag"
                        >
                            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="15 18 9 12 15 6" />
                            </svg>
                        </button>
                        <input
                            type="date"
                            value={selectedDate}
                            onChange={e => setSelectedDate(e.target.value)}
                            max={formatDate(new Date())}
                            className="px-3 py-2 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30 backdrop-blur-sm"
                        />
                        <button
                            onClick={() => shiftDay(1)}
                            disabled={isToday}
                            className="p-2 rounded-xl bg-black/[0.03] hover:bg-black/[0.06] text-gray-500 transition-colors disabled:opacity-30"
                            title="Nächster Tag"
                        >
                            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="9 18 15 12 9 6" />
                            </svg>
                        </button>
                        <span className="text-sm text-gray-500 hidden sm:inline">{friendlyDate(selectedDate)}</span>
                    </div>

                    {/* Resolution Selector */}
                    <div className="flex bg-black/[0.03] rounded-xl p-0.5">
                        {(['raw', '1m', '5m', '1h', '1d'] as Resolution[]).map(res => (
                            <button
                                key={res}
                                onClick={() => setResolution(res)}
                                className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-all duration-200 ${
                                    resolution === res
                                        ? 'bg-white text-gray-800 shadow-sm'
                                        : 'text-gray-400 hover:text-gray-600'
                                }`}
                            >
                                {res === 'raw' ? 'Roh' : res}
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            {/* Charts */}
            <div>
                {loadingData ? (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        {[1, 2, 3, 4].map(i => (
                            <div key={i} className="animate-pulse h-56 bg-black/[0.03] rounded-2xl" />
                        ))}
                    </div>
                ) : sensorData.length === 0 ? (
                    <div className="glass-card py-16 text-center text-gray-400 text-sm">
                        Keine Messdaten für diesen Tag vorhanden
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        {sensorData.filter(s => s.kind in KIND_CONFIG).map(series => {
                            const config = KIND_CONFIG[series.kind];
                            const latestValue = series.data.length > 0
                                ? series.data[series.data.length - 1].value
                                : null;

                            return (
                                <div
                                    key={series.kind}
                                    className="bg-white/40 rounded-2xl p-4 border border-black/[0.04] backdrop-blur-sm"
                                >
                                    <div className="flex items-center justify-between mb-3">
                                        <div className="flex items-center gap-2">
                                            <span className="text-lg">{config.icon}</span>
                                            <span className="text-sm font-medium text-gray-700">{config.label}</span>
                                        </div>
                                        {latestValue !== null && (
                                            <div className="text-right">
                                                <span className="text-xl font-bold" style={{ color: config.color }}>
                                                    {latestValue}
                                                </span>
                                                <span className="text-xs text-gray-400 ml-1">{config.unit}</span>
                                            </div>
                                        )}
                                    </div>

                                    {series.data.length > 0 ? (
                                        <ResponsiveContainer width="100%" height={180}>
                                            <LineChart data={series.data}>
                                                <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,0.04)" vertical={false} />
                                                <XAxis
                                                    dataKey="timestamp"
                                                    tickFormatter={(t) => {
                                                        const d = new Date(t);
                                                        return d.toLocaleTimeString('de-CH', { hour: '2-digit', minute: '2-digit' });
                                                    }}
                                                    tick={{ fontSize: 10, fill: '#94a3b8' }}
                                                    axisLine={{ stroke: 'rgba(0,0,0,0.04)' }}
                                                    tickLine={false}
                                                    interval="preserveStartEnd"
                                                    minTickGap={40}
                                                />
                                                <YAxis
                                                    tick={{ fontSize: 10, fill: '#94a3b8' }}
                                                    axisLine={false}
                                                    tickLine={false}
                                                    width={40}
                                                    domain={['auto', 'auto']}
                                                />
                                                <Tooltip
                                                    contentStyle={{
                                                        borderRadius: '12px',
                                                        border: '1px solid rgba(0,0,0,0.06)',
                                                        boxShadow: '0 4px 24px rgba(0,0,0,0.06)',
                                                        fontSize: '11px',
                                                        padding: '8px 12px',
                                                        background: 'rgba(255,255,255,0.9)',
                                                        backdropFilter: 'blur(8px)',
                                                    }}
                                                    labelFormatter={(t) => new Date(t as string).toLocaleString('de-CH')}
                                                    formatter={(value: number) => [`${value} ${config.unit}`, config.label]}
                                                />
                                                <Line
                                                    type="monotone"
                                                    dataKey="value"
                                                    stroke={config.color}
                                                    strokeWidth={2}
                                                    dot={false}
                                                    activeDot={{ r: 3, fill: config.color, stroke: '#fff', strokeWidth: 2 }}
                                                />
                                            </LineChart>
                                        </ResponsiveContainer>
                                    ) : (
                                        <div className="h-44 flex items-center justify-center text-gray-300 text-xs">
                                            Keine Daten
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>

            {/* Data points count */}
            {!loadingData && sensorData.length > 0 && (
                <div className="text-center text-xs text-gray-400">
                    {sensorData.reduce((sum, s) => sum + s.data.length, 0).toLocaleString()} Datenpunkte · Auflösung: {resolution === 'raw' ? 'Rohdaten' : resolution}
                </div>
            )}
        </div>
    );
}
