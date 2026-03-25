'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiListSensors, apiGetSensorData, apiExportSensorData, SensorInfo, SensorDataResponse } from '@/lib/api';
import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';

type TimeRange = 'live' | '1h' | '24h' | '7d' | '30d';

const KIND_CONFIG: Record<string, { label: string; unit: string; color: string; icon: string }> = {
    temperature: { label: 'Temperatur', unit: '°C', color: '#ef4444', icon: '🌡' },
    humidity: { label: 'Luftfeuchtigkeit', unit: '%', color: '#3b82f6', icon: '💧' },
    soil_moisture: { label: 'Bodenfeuchtigkeit', unit: '%', color: '#a855f7', icon: '🌱' },
    bioelectric: { label: 'Bioelektrisches Signal', unit: 'mV', color: '#10b981', icon: '⚡' },
};

type ExportStatus = 'idle' | 'loading' | 'zipping' | 'done' | 'error';

export default function SensorsPage() {
    const [sensors, setSensors] = useState<SensorInfo[]>([]);
    const [loading, setLoading] = useState(true);
    const [selectedSensor, setSelectedSensor] = useState<string | null>(null);
    const [sensorData, setSensorData] = useState<SensorDataResponse[]>([]);
    const [loadingData, setLoadingData] = useState(false);
    const [timeRange, setTimeRange] = useState<TimeRange>('24h');
    const [exportStatus, setExportStatus] = useState<ExportStatus>('idle');

    useEffect(() => {
        apiListSensors()
            .then(setSensors)
            .catch(console.error)
            .finally(() => setLoading(false));
    }, []);

    const loadSensorData = useCallback(async (sensorId: string, range: TimeRange) => {
        setLoadingData(true);
        try {
            const apiRange = range === 'live' ? '1h' : range;
            const data = await apiGetSensorData(sensorId, apiRange);
            setSensorData(data);
        } catch (err) {
            console.error('Failed to load sensor data:', err);
            setSensorData([]);
        } finally {
            setLoadingData(false);
        }
    }, []);

    const handleSensorClick = (sensorId: string) => {
        if (selectedSensor === sensorId) {
            setSelectedSensor(null);
            setSensorData([]);
        } else {
            setSelectedSensor(sensorId);
            loadSensorData(sensorId, timeRange);
        }
    };

    const handleRangeChange = (range: TimeRange) => {
        setTimeRange(range);
        if (selectedSensor) {
            loadSensorData(selectedSensor, range);
        }
    };

    const handleExport = async () => {
        if (!selectedSensor) return;
        setExportStatus('loading');
        try {
            setExportStatus('zipping');
            await apiExportSensorData(selectedSensor, timeRange === 'live' ? '1h' : timeRange);
            setExportStatus('done');
            setTimeout(() => setExportStatus('idle'), 2000);
        } catch (err) {
            console.error('Export failed:', err);
            setExportStatus('error');
            setTimeout(() => setExportStatus('idle'), 3000);
        }
    };

    // Auto-refresh data every 5s when live mode is active
    useEffect(() => {
        if (!selectedSensor || timeRange !== 'live') return;
        const interval = setInterval(() => {
            loadSensorData(selectedSensor, 'live');
        }, 5000);
        return () => clearInterval(interval);
    }, [selectedSensor, timeRange, loadSensorData]);

    if (loading) {
        return (
            <div className="animate-pulse space-y-4">
                <div className="h-8 w-32 bg-black/[0.04] rounded-xl" />
                <div className="h-80 bg-black/[0.04] rounded-2xl" />
            </div>
        );
    }

    const selectedSensorInfo = sensors.find(s => s.id === selectedSensor);

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-gray-800 tracking-tight">Sensoren</h1>
                <p className="text-sm text-gray-400 mt-1">ESP32-Sensormodule – klicke auf einen Sensor für Live-Daten</p>
            </div>

            {sensors.length === 0 ? (
                <div className="glass-card p-12 text-center">
                    <div className="text-4xl mb-4">📡</div>
                    <h3 className="text-lg font-semibold text-gray-800 mb-2">Keine Sensoren registriert</h3>
                    <p className="text-sm text-gray-400">
                        Sensoren werden automatisch via mDNS erkannt, wenn ein Gateway verbunden ist.
                    </p>
                </div>
            ) : (
                <div className="space-y-4">
                    {/* Sensor List — Desktop Table */}
                    <div className="glass-table hidden sm:block">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="border-b border-black/[0.04]">
                                    <th className="text-left px-5 py-3.5 text-[11px] font-medium text-gray-400 uppercase tracking-wider">Name</th>
                                    <th className="text-left px-5 py-3.5 text-[11px] font-medium text-gray-400 uppercase tracking-wider">MAC</th>
                                    <th className="text-left px-5 py-3.5 text-[11px] font-medium text-gray-400 uppercase tracking-wider">Gateway</th>
                                    <th className="text-left px-5 py-3.5 text-[11px] font-medium text-gray-400 uppercase tracking-wider">Status</th>
                                    <th className="text-left px-5 py-3.5 text-[11px] font-medium text-gray-400 uppercase tracking-wider">Zuletzt</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-black/[0.03]">
                                {sensors.map(s => (
                                    <tr
                                        key={s.id}
                                        onClick={() => handleSensorClick(s.id)}
                                        className={`cursor-pointer transition-all duration-200 ${
                                            selectedSensor === s.id
                                                ? 'bg-emerald-50/60'
                                                : 'hover:bg-white/50'
                                        }`}
                                    >
                                        <td className="px-5 py-3.5 font-medium text-gray-800">
                                            <div className="flex items-center gap-2">
                                                <span className={`transition-transform duration-200 text-xs text-gray-300 ${selectedSensor === s.id ? 'rotate-90' : ''}`}>▶</span>
                                                {s.name || s.mac_address}
                                            </div>
                                        </td>
                                        <td className="px-5 py-3.5 font-mono text-gray-400 text-xs">{s.mac_address}</td>
                                        <td className="px-5 py-3.5 text-gray-500">{s.gateway_name || '–'}</td>
                                        <td className="px-5 py-3.5">
                                            <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
                                                s.status === 'online'
                                                    ? 'bg-emerald-50 text-emerald-600'
                                                    : 'bg-gray-100 text-gray-400'
                                            }`}>
                                                <span className={`w-1.5 h-1.5 rounded-full ${s.status === 'online' ? 'bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.4)]' : 'bg-gray-300'}`} />
                                                {s.status}
                                            </span>
                                        </td>
                                        <td className="px-5 py-3.5 text-xs text-gray-400">
                                            {s.last_seen ? new Date(s.last_seen).toLocaleString('de-CH') : '–'}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    {/* Sensor List — Mobile Cards */}
                    <div className="space-y-2 sm:hidden">
                        {sensors.map(s => (
                            <div
                                key={s.id}
                                onClick={() => handleSensorClick(s.id)}
                                className={`glass-card p-4 cursor-pointer ${
                                    selectedSensor === s.id ? 'border-emerald-500/20' : ''
                                }`}
                            >
                                <div className="flex items-center justify-between mb-1">
                                    <span className="text-sm font-medium text-gray-800">{s.name || s.mac_address}</span>
                                    <span className={`w-2 h-2 rounded-full ${s.status === 'online' ? 'bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.4)]' : 'bg-gray-300'}`} />
                                </div>
                                <div className="flex items-center gap-3 text-xs text-gray-400">
                                    <span className="font-mono">{s.mac_address}</span>
                                    <span>·</span>
                                    <span>{s.gateway_name || '–'}</span>
                                </div>
                            </div>
                        ))}
                    </div>

                    {/* Sensor Detail Panel */}
                    {selectedSensor && selectedSensorInfo && (
                        <div className="glass-card overflow-hidden">
                            {/* Header */}
                            <div className="px-4 sm:px-6 py-4 border-b border-black/[0.04] flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                                <div>
                                    <h2 className="text-lg font-semibold text-gray-800">
                                        {selectedSensorInfo.name || selectedSensorInfo.mac_address}
                                    </h2>
                                    <p className="text-xs text-gray-400 mt-0.5">
                                        {selectedSensorInfo.mac_address} · {selectedSensorInfo.gateway_name}
                                    </p>
                                </div>

                                <div className="flex items-center gap-2">
                                    {/* Time Range Segmented Control */}
                                    <div className="flex bg-black/[0.03] rounded-xl p-0.5">
                                        {(['live', '1h', '24h', '7d', '30d'] as TimeRange[]).map((range) => (
                                            <button
                                                key={range}
                                                onClick={() => handleRangeChange(range)}
                                                className={`px-3 sm:px-4 py-1.5 text-xs font-medium rounded-lg transition-all duration-200 ${
                                                    timeRange === range
                                                        ? range === 'live'
                                                            ? 'bg-emerald-500 text-white shadow-sm'
                                                            : 'bg-white text-gray-800 shadow-sm'
                                                        : 'text-gray-400 hover:text-gray-600'
                                                }`}
                                            >
                                                {range === 'live' ? (
                                                    <span className="flex items-center gap-1.5">
                                                        <span className={`w-1.5 h-1.5 rounded-full ${timeRange === 'live' ? 'bg-white animate-pulse' : 'bg-emerald-500'}`} />
                                                        Live
                                                    </span>
                                                ) : range}
                                            </button>
                                        ))}
                                    </div>

                                    {/* Export Button */}
                                    <button
                                        onClick={handleExport}
                                        disabled={exportStatus !== 'idle'}
                                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-emerald-50 text-emerald-700 hover:bg-emerald-100 transition-all duration-200 disabled:opacity-50 border border-emerald-200/50"
                                        title="Sensordaten als ZIP exportieren"
                                    >
                                        {exportStatus === 'idle' && (
                                            <>
                                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                                    <polyline points="7 10 12 15 17 10" />
                                                    <line x1="12" y1="15" x2="12" y2="3" />
                                                </svg>
                                                Export
                                            </>
                                        )}
                                        {exportStatus === 'loading' && 'Lade…'}
                                        {exportStatus === 'zipping' && 'Zipping…'}
                                        {exportStatus === 'done' && '✓ Fertig'}
                                        {exportStatus === 'error' && '✗ Fehler'}
                                    </button>
                                </div>
                            </div>

                            {/* Export Progress */}
                            {(exportStatus === 'loading' || exportStatus === 'zipping') && (
                                <div className="export-progress">
                                    <div
                                        className="export-progress-bar"
                                        style={{ width: exportStatus === 'loading' ? '40%' : '80%' }}
                                    />
                                </div>
                            )}

                            {/* Charts */}
                            <div className="p-4 sm:p-6">
                                {loadingData ? (
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                        {[1, 2, 3, 4].map(i => (
                                            <div key={i} className="animate-pulse h-56 bg-black/[0.03] rounded-2xl" />
                                        ))}
                                    </div>
                                ) : sensorData.length === 0 ? (
                                    <div className="py-16 text-center text-gray-400 text-sm">
                                        Keine Messdaten für diesen Zeitraum vorhanden
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
                                                    {/* Chart Header */}
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

                                                    {/* Chart */}
                                                    {series.data.length > 0 ? (
                                                        <ResponsiveContainer width="100%" height={160}>
                                                            <LineChart data={series.data}>
                                                                <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,0.04)" vertical={false} />
                                                                <XAxis
                                                                    dataKey="timestamp"
                                                                    tickFormatter={(t) => {
                                                                        const d = new Date(t);
                                                                        if (timeRange === '1h' || timeRange === '24h' || timeRange === 'live') {
                                                                            return d.toLocaleTimeString('de-CH', { hour: '2-digit', minute: '2-digit' });
                                                                        }
                                                                        return d.toLocaleDateString('de-CH', { day: '2-digit', month: '2-digit' });
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
                                                        <div className="h-40 flex items-center justify-center text-gray-300 text-xs">
                                                            Keine Daten
                                                        </div>
                                                    )}
                                                </div>
                                            );
                                        })}
                                    </div>
                                )}

                                {/* Live indicator */}
                                {timeRange === 'live' && !loadingData && sensorData.length > 0 && (
                                    <div className="mt-4 flex items-center justify-center gap-2 text-xs text-gray-400">
                                        <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.4)]" />
                                        Live – aktualisiert alle 5s
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
