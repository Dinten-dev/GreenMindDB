'use client';

import { useState, useEffect } from 'react';
import { apiListSensors, apiGetSensorData, SensorInfo, SensorData } from '@/lib/api';
import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';

type TimeRange = '24h' | '7d' | '30d';

const COLORS = ['#34c759', '#007aff', '#ff9500', '#af52de', '#ff3b30'];

export default function SensorsPage() {
    const [sensors, setSensors] = useState<SensorInfo[]>([]);
    const [loading, setLoading] = useState(true);
    const [selectedSensor, setSelectedSensor] = useState<string | null>(null);
    const [sensorData, setSensorData] = useState<SensorData | null>(null);
    const [timeRange, setTimeRange] = useState<TimeRange>('24h');
    const [chartLoading, setChartLoading] = useState(false);

    useEffect(() => {
        apiListSensors()
            .then(data => {
                setSensors(data);
                if (data.length > 0) setSelectedSensor(data[0].id);
            })
            .catch(console.error)
            .finally(() => setLoading(false));
    }, []);

    useEffect(() => {
        if (!selectedSensor) return;
        setChartLoading(true);
        apiGetSensorData(selectedSensor, timeRange)
            .then(setSensorData)
            .catch(console.error)
            .finally(() => setChartLoading(false));
    }, [selectedSensor, timeRange]);

    if (loading) {
        return (
            <div className="animate-pulse space-y-4">
                <div className="h-8 w-32 bg-apple-gray-200 rounded-lg" />
                <div className="h-80 bg-apple-gray-200 rounded-apple-lg" />
            </div>
        );
    }

    const currentSensor = sensors.find(s => s.id === selectedSensor);

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-apple-gray-800">Sensors</h1>
                <p className="text-sm text-apple-gray-400 mt-1">View sensor data and trends</p>
            </div>

            {sensors.length === 0 ? (
                <div className="bg-white rounded-apple-lg shadow-apple-card p-12 text-center">
                    <div className="text-4xl mb-4">◈</div>
                    <h3 className="text-lg font-semibold text-apple-gray-800 mb-2">No sensors registered</h3>
                    <p className="text-sm text-apple-gray-400">
                        Sensors are automatically created when devices send data.
                    </p>
                </div>
            ) : (
                <div className="grid lg:grid-cols-[280px_1fr] gap-6">
                    {/* Sensor List */}
                    <div className="bg-white rounded-apple-lg shadow-apple-card p-4 h-fit">
                        <h2 className="text-sm font-medium text-apple-gray-400 uppercase tracking-wide px-3 mb-3">All Sensors</h2>
                        <div className="space-y-0.5">
                            {sensors.map((s, i) => (
                                <button
                                    key={s.id}
                                    onClick={() => setSelectedSensor(s.id)}
                                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left text-sm transition-all duration-150 ${selectedSensor === s.id
                                            ? 'bg-gm-green-50 text-gm-green-700'
                                            : 'text-apple-gray-600 hover:bg-apple-gray-100'
                                        }`}
                                >
                                    <span
                                        className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                                        style={{ backgroundColor: COLORS[i % COLORS.length] }}
                                    />
                                    <div className="min-w-0 flex-1">
                                        <p className="font-medium truncate">{s.label || s.kind}</p>
                                        <p className="text-xs text-apple-gray-400">{s.unit} · {s.device_name || s.device_serial}</p>
                                    </div>
                                    {s.device_status && (
                                        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${s.device_status === 'online' ? 'bg-gm-green-500' : 'bg-apple-gray-300'
                                            }`} />
                                    )}
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Chart */}
                    <div className="bg-white rounded-apple-lg shadow-apple-card p-6">
                        <div className="flex items-center justify-between mb-6">
                            <div>
                                <h2 className="text-lg font-semibold text-apple-gray-800">
                                    {currentSensor?.label || currentSensor?.kind || 'Sensor Data'}
                                </h2>
                                <p className="text-xs text-apple-gray-400 mt-0.5">
                                    {currentSensor?.device_name} · {currentSensor?.unit}
                                </p>
                            </div>
                            <div className="flex bg-apple-gray-100 rounded-lg p-0.5">
                                {(['24h', '7d', '30d'] as TimeRange[]).map((range) => (
                                    <button
                                        key={range}
                                        onClick={() => setTimeRange(range)}
                                        className={`px-4 py-1.5 text-xs font-medium rounded-md transition-all duration-200 ${timeRange === range
                                                ? 'bg-white text-apple-gray-800 shadow-sm'
                                                : 'text-apple-gray-400 hover:text-apple-gray-600'
                                            }`}
                                    >
                                        {range}
                                    </button>
                                ))}
                            </div>
                        </div>

                        {chartLoading ? (
                            <div className="h-80 flex items-center justify-center">
                                <div className="text-sm text-apple-gray-400">Loading…</div>
                            </div>
                        ) : sensorData && sensorData.data.length > 0 ? (
                            <ResponsiveContainer width="100%" height={400}>
                                <LineChart data={sensorData.data}>
                                    <CartesianGrid strokeDasharray="3 3" stroke="#e8e8ed" vertical={false} />
                                    <XAxis
                                        dataKey="timestamp"
                                        tickFormatter={(t) => {
                                            const d = new Date(t);
                                            return timeRange === '24h'
                                                ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                                                : d.toLocaleDateString([], { month: 'short', day: 'numeric' });
                                        }}
                                        tick={{ fontSize: 11, fill: '#86868b' }}
                                        axisLine={{ stroke: '#e8e8ed' }}
                                        tickLine={false}
                                        interval="preserveStartEnd"
                                    />
                                    <YAxis
                                        tick={{ fontSize: 11, fill: '#86868b' }}
                                        axisLine={false}
                                        tickLine={false}
                                        width={48}
                                    />
                                    <Tooltip
                                        contentStyle={{
                                            borderRadius: '12px',
                                            border: '1px solid #e8e8ed',
                                            boxShadow: '0 4px 16px rgba(0,0,0,0.06)',
                                            fontSize: '12px',
                                        }}
                                        labelFormatter={(t) => new Date(t as string).toLocaleString()}
                                        formatter={(value: number) => [`${value} ${sensorData.unit}`, sensorData.kind]}
                                    />
                                    <Line
                                        type="monotone"
                                        dataKey="value"
                                        stroke={COLORS[sensors.findIndex(s => s.id === selectedSensor) % COLORS.length]}
                                        strokeWidth={2}
                                        dot={false}
                                        activeDot={{ r: 4, stroke: '#fff', strokeWidth: 2 }}
                                    />
                                </LineChart>
                            </ResponsiveContainer>
                        ) : (
                            <div className="h-80 flex items-center justify-center text-apple-gray-400 text-sm">
                                No data for this time range
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
