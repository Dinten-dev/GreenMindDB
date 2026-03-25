'use client';

import { useState, useEffect, useCallback } from 'react';
import { apiListSensors, apiGetSensorData, SensorInfo, SensorDataResponse } from '@/lib/api';
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

export default function SensorsPage() {
    const [sensors, setSensors] = useState<SensorInfo[]>([]);
    const [loading, setLoading] = useState(true);
    const [selectedSensor, setSelectedSensor] = useState<string | null>(null);
    const [sensorData, setSensorData] = useState<SensorDataResponse[]>([]);
    const [loadingData, setLoadingData] = useState(false);
    const [timeRange, setTimeRange] = useState<TimeRange>('24h');

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
                <div className="h-8 w-32 bg-apple-gray-200 rounded-lg" />
                <div className="h-80 bg-apple-gray-200 rounded-apple-lg" />
            </div>
        );
    }

    const selectedSensorInfo = sensors.find(s => s.id === selectedSensor);

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-apple-gray-800">Sensoren</h1>
                <p className="text-sm text-apple-gray-400 mt-1">ESP32-Sensormodule – klicke auf einen Sensor für Live-Daten</p>
            </div>

            {sensors.length === 0 ? (
                <div className="bg-white rounded-apple-lg shadow-apple-card p-12 text-center">
                    <div className="text-4xl mb-4">📡</div>
                    <h3 className="text-lg font-semibold text-apple-gray-800 mb-2">Keine Sensoren registriert</h3>
                    <p className="text-sm text-apple-gray-400">
                        Sensoren werden automatisch via mDNS erkannt, wenn ein Gateway verbunden ist.
                    </p>
                </div>
            ) : (
                <div className="space-y-4">
                    {/* Sensor List */}
                    <div className="bg-white rounded-apple-lg shadow-apple-card overflow-hidden">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="border-b border-apple-gray-100">
                                    <th className="text-left px-5 py-3 text-xs font-medium text-apple-gray-400 uppercase tracking-wide">Name</th>
                                    <th className="text-left px-5 py-3 text-xs font-medium text-apple-gray-400 uppercase tracking-wide">MAC</th>
                                    <th className="text-left px-5 py-3 text-xs font-medium text-apple-gray-400 uppercase tracking-wide">Gateway</th>
                                    <th className="text-left px-5 py-3 text-xs font-medium text-apple-gray-400 uppercase tracking-wide">Status</th>
                                    <th className="text-left px-5 py-3 text-xs font-medium text-apple-gray-400 uppercase tracking-wide">Zuletzt</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-apple-gray-50">
                                {sensors.map(s => (
                                    <tr
                                        key={s.id}
                                        onClick={() => handleSensorClick(s.id)}
                                        className={`cursor-pointer transition-colors ${
                                            selectedSensor === s.id
                                                ? 'bg-gm-green-50'
                                                : 'hover:bg-apple-gray-50'
                                        }`}
                                    >
                                        <td className="px-5 py-3.5 font-medium text-apple-gray-800">
                                            <div className="flex items-center gap-2">
                                                <span className={`transition-transform duration-200 text-xs ${selectedSensor === s.id ? 'rotate-90' : ''}`}>▶</span>
                                                {s.name || s.mac_address}
                                            </div>
                                        </td>
                                        <td className="px-5 py-3.5 font-mono text-apple-gray-500 text-xs">{s.mac_address}</td>
                                        <td className="px-5 py-3.5 text-apple-gray-500">{s.gateway_name || '–'}</td>
                                        <td className="px-5 py-3.5">
                                            <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
                                                s.status === 'online'
                                                    ? 'bg-gm-green-50 text-gm-green-600'
                                                    : 'bg-apple-gray-100 text-apple-gray-400'
                                            }`}>
                                                <span className={`w-1.5 h-1.5 rounded-full ${s.status === 'online' ? 'bg-gm-green-500' : 'bg-apple-gray-300'}`} />
                                                {s.status}
                                            </span>
                                        </td>
                                        <td className="px-5 py-3.5 text-xs text-apple-gray-400">
                                            {s.last_seen ? new Date(s.last_seen).toLocaleString('de-CH') : '–'}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    {/* Sensor Detail Panel */}
                    {selectedSensor && selectedSensorInfo && (
                        <div className="bg-white rounded-apple-lg shadow-apple-card overflow-hidden animate-in">
                            {/* Header */}
                            <div className="px-6 py-4 border-b border-apple-gray-100 flex items-center justify-between">
                                <div>
                                    <h2 className="text-lg font-semibold text-apple-gray-800">
                                        {selectedSensorInfo.name || selectedSensorInfo.mac_address}
                                    </h2>
                                    <p className="text-xs text-apple-gray-400 mt-0.5">
                                        {selectedSensorInfo.mac_address} · {selectedSensorInfo.gateway_name}
                                    </p>
                                </div>

                                {/* Time Range Segmented Control */}
                                <div className="flex bg-apple-gray-100 rounded-lg p-0.5">
                                    {(['live', '1h', '24h', '7d', '30d'] as TimeRange[]).map((range) => (
                                        <button
                                            key={range}
                                            onClick={() => handleRangeChange(range)}
                                            className={`px-4 py-1.5 text-xs font-medium rounded-md transition-all duration-200 ${
                                                timeRange === range
                                                    ? range === 'live'
                                                        ? 'bg-gm-green-500 text-white shadow-sm'
                                                        : 'bg-white text-apple-gray-800 shadow-sm'
                                                    : 'text-apple-gray-400 hover:text-apple-gray-600'
                                            }`}
                                        >
                                            {range === 'live' ? (
                                                <span className="flex items-center gap-1.5">
                                                    <span className={`w-1.5 h-1.5 rounded-full ${timeRange === 'live' ? 'bg-white animate-pulse' : 'bg-gm-green-500'}`} />
                                                    Live
                                                </span>
                                            ) : range}
                                        </button>
                                    ))}
                                </div>
                            </div>

                            {/* Charts */}
                            <div className="p-6">
                                {loadingData ? (
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                        {[1, 2, 3, 4].map(i => (
                                            <div key={i} className="animate-pulse h-56 bg-apple-gray-100 rounded-apple-lg" />
                                        ))}
                                    </div>
                                ) : sensorData.length === 0 ? (
                                    <div className="py-16 text-center text-apple-gray-400 text-sm">
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
                                                    className="bg-apple-gray-50/50 rounded-apple-lg p-4 border border-apple-gray-100"
                                                >
                                                    {/* Chart Header */}
                                                    <div className="flex items-center justify-between mb-3">
                                                        <div className="flex items-center gap-2">
                                                            <span className="text-lg">{config.icon}</span>
                                                            <span className="text-sm font-medium text-apple-gray-700">{config.label}</span>
                                                        </div>
                                                        {latestValue !== null && (
                                                            <div className="text-right">
                                                                <span className="text-xl font-bold" style={{ color: config.color }}>
                                                                    {latestValue}
                                                                </span>
                                                                <span className="text-xs text-apple-gray-400 ml-1">{config.unit}</span>
                                                            </div>
                                                        )}
                                                    </div>

                                                    {/* Chart */}
                                                    {series.data.length > 0 ? (
                                                        <ResponsiveContainer width="100%" height={160}>
                                                            <LineChart data={series.data}>
                                                                <CartesianGrid strokeDasharray="3 3" stroke="#e8e8ed" vertical={false} />
                                                                <XAxis
                                                                    dataKey="timestamp"
                                                                    tickFormatter={(t) => {
                                                                        const d = new Date(t);
                                                                        if (timeRange === '1h' || timeRange === '24h') {
                                                                            return d.toLocaleTimeString('de-CH', { hour: '2-digit', minute: '2-digit' });
                                                                        }
                                                                        return d.toLocaleDateString('de-CH', { day: '2-digit', month: '2-digit' });
                                                                    }}
                                                                    tick={{ fontSize: 10, fill: '#86868b' }}
                                                                    axisLine={{ stroke: '#e8e8ed' }}
                                                                    tickLine={false}
                                                                    interval="preserveStartEnd"
                                                                    minTickGap={40}
                                                                />
                                                                <YAxis
                                                                    tick={{ fontSize: 10, fill: '#86868b' }}
                                                                    axisLine={false}
                                                                    tickLine={false}
                                                                    width={40}
                                                                    domain={['auto', 'auto']}
                                                                />
                                                                <Tooltip
                                                                    contentStyle={{
                                                                        borderRadius: '10px',
                                                                        border: '1px solid #e8e8ed',
                                                                        boxShadow: '0 4px 16px rgba(0,0,0,0.06)',
                                                                        fontSize: '11px',
                                                                        padding: '8px 12px',
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
                                                        <div className="h-40 flex items-center justify-center text-apple-gray-300 text-xs">
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
                                    <div className="mt-4 flex items-center justify-center gap-2 text-xs text-apple-gray-400">
                                        <span className="w-2 h-2 rounded-full bg-gm-green-500 animate-pulse" />
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
