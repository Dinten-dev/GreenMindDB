'use client';

import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import {
    apiListGreenhouses, apiListDevices, apiListSensors,
    apiGetSensorData,
    Greenhouse, DeviceInfo, SensorInfo, SensorData
} from '@/lib/api';
import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';

type TimeRange = '24h' | '7d' | '30d';

export default function DashboardPage() {
    const { user, createOrg, refresh } = useAuth();
    const [greenhouses, setGreenhouses] = useState<Greenhouse[]>([]);
    const [devices, setDevices] = useState<DeviceInfo[]>([]);
    const [sensors, setSensors] = useState<SensorInfo[]>([]);
    const [selectedSensor, setSelectedSensor] = useState<string | null>(null);
    const [sensorData, setSensorData] = useState<SensorData | null>(null);
    const [timeRange, setTimeRange] = useState<TimeRange>('24h');
    const [loading, setLoading] = useState(true);
    const [orgName, setOrgName] = useState('');
    const [creatingOrg, setCreatingOrg] = useState(false);

    const loadData = useCallback(async () => {
        try {
            const [gh, dev, sen] = await Promise.all([
                apiListGreenhouses(),
                apiListDevices(),
                apiListSensors(),
            ]);
            setGreenhouses(gh);
            setDevices(dev);
            setSensors(sen);
            if (sen.length > 0 && !selectedSensor) {
                setSelectedSensor(sen[0].id);
            }
        } catch (err) {
            console.error('Dashboard load error:', err);
        } finally {
            setLoading(false);
        }
    }, [selectedSensor]);

    useEffect(() => {
        if (user?.organization_id) loadData();
        else setLoading(false);
    }, [user?.organization_id, loadData]);

    // Load sensor chart data when sensor or range changes
    useEffect(() => {
        if (!selectedSensor) return;
        apiGetSensorData(selectedSensor, timeRange)
            .then(setSensorData)
            .catch(console.error);
    }, [selectedSensor, timeRange]);

    const handleCreateOrg = async () => {
        if (!orgName.trim()) return;
        setCreatingOrg(true);
        try {
            await createOrg(orgName);
            await refresh();
        } catch (err) {
            console.error(err);
        } finally {
            setCreatingOrg(false);
        }
    };

    // ── No Organization ──
    if (!loading && !user?.organization_id) {
        return (
            <div className="max-w-md mx-auto mt-24">
                <div className="bg-white rounded-apple-lg shadow-apple p-8 text-center">
                    <div className="text-4xl mb-4">🏢</div>
                    <h2 className="text-xl font-semibold text-apple-gray-800 mb-2">Create your Organization</h2>
                    <p className="text-sm text-apple-gray-400 mb-6">
                        An organization groups your greenhouses, devices, and team members.
                    </p>
                    <input
                        type="text"
                        value={orgName}
                        onChange={(e) => setOrgName(e.target.value)}
                        className="w-full px-4 py-2.5 rounded-apple bg-apple-gray-100 border border-apple-gray-200 text-sm text-apple-gray-800 placeholder-apple-gray-400 focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:border-transparent mb-4"
                        placeholder="Organization name"
                    />
                    <button
                        onClick={handleCreateOrg}
                        disabled={creatingOrg || !orgName.trim()}
                        className="w-full py-2.5 bg-gm-green-500 text-white rounded-apple font-medium text-sm hover:bg-gm-green-600 transition-colors disabled:opacity-50"
                    >
                        {creatingOrg ? 'Creating…' : 'Create Organization'}
                    </button>
                </div>
            </div>
        );
    }

    if (loading) {
        return (
            <div className="animate-pulse space-y-6">
                <div className="h-8 w-48 bg-apple-gray-200 rounded-lg" />
                <div className="grid grid-cols-4 gap-4">
                    {[1, 2, 3, 4].map(i => <div key={i} className="h-24 bg-apple-gray-200 rounded-apple-lg" />)}
                </div>
                <div className="h-80 bg-apple-gray-200 rounded-apple-lg" />
            </div>
        );
    }

    const onlineDevices = devices.filter(d => d.status === 'online').length;

    return (
        <div className="space-y-8">
            {/* Header */}
            <div>
                <h1 className="text-2xl font-bold text-apple-gray-800">Dashboard</h1>
                <p className="text-sm text-apple-gray-400 mt-1">
                    {user?.organization_name || 'Your organization'} overview
                </p>
            </div>

            {/* Stat Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <StatCard label="Greenhouses" value={greenhouses.length} icon="⌂" />
                <StatCard label="Devices" value={devices.length} sub={`${onlineDevices} online`} icon="◎" />
                <StatCard label="Sensors" value={sensors.length} icon="◈" />
                <StatCard label="Data Points" value={sensorData?.data.length || 0} sub={timeRange} icon="◉" accent />
            </div>

            {/* Sensor Chart */}
            {sensors.length > 0 ? (
                <div className="bg-white rounded-apple-lg shadow-apple-card p-6">
                    <div className="flex items-center justify-between mb-6">
                        <div className="flex items-center gap-4">
                            <h2 className="text-lg font-semibold text-apple-gray-800">Sensor Data</h2>
                            <select
                                value={selectedSensor || ''}
                                onChange={(e) => setSelectedSensor(e.target.value)}
                                className="text-sm px-3 py-1.5 rounded-lg bg-apple-gray-100 border border-apple-gray-200 text-apple-gray-600 focus:outline-none focus:ring-2 focus:ring-gm-green-500"
                            >
                                {sensors.map(s => (
                                    <option key={s.id} value={s.id}>
                                        {s.label || `${s.kind} (${s.unit})`}
                                    </option>
                                ))}
                            </select>
                        </div>

                        {/* Segmented Control */}
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

                    {/* Chart */}
                    {sensorData && sensorData.data.length > 0 ? (
                        <ResponsiveContainer width="100%" height={320}>
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
                                    stroke="#34c759"
                                    strokeWidth={2}
                                    dot={false}
                                    activeDot={{ r: 4, fill: '#34c759', stroke: '#fff', strokeWidth: 2 }}
                                />
                            </LineChart>
                        </ResponsiveContainer>
                    ) : (
                        <div className="h-80 flex items-center justify-center text-apple-gray-400 text-sm">
                            No data for this time range
                        </div>
                    )}
                </div>
            ) : (
                <div className="bg-white rounded-apple-lg shadow-apple-card p-12 text-center">
                    <div className="text-4xl mb-4">📊</div>
                    <h3 className="text-lg font-semibold text-apple-gray-800 mb-2">No sensor data yet</h3>
                    <p className="text-sm text-apple-gray-400">
                        Create a greenhouse, pair a device, and start sending data to see charts here.
                    </p>
                </div>
            )}

            {/* Device Status */}
            {devices.length > 0 && (
                <div className="bg-white rounded-apple-lg shadow-apple-card p-6">
                    <h2 className="text-lg font-semibold text-apple-gray-800 mb-4">Devices</h2>
                    <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-3">
                        {devices.map(dev => (
                            <div key={dev.id} className="flex items-center gap-3 px-4 py-3 bg-apple-gray-100/50 rounded-apple">
                                <span className={`w-2 h-2 rounded-full ${dev.status === 'online' ? 'bg-gm-green-500' : 'bg-apple-gray-300'}`} />
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium text-apple-gray-800 truncate">{dev.name || dev.serial}</p>
                                    <p className="text-xs text-apple-gray-400">{dev.sensor_count} sensors · {dev.greenhouse_name}</p>
                                </div>
                                <span className="text-xs text-apple-gray-400">
                                    {dev.last_seen ? timeAgo(dev.last_seen) : 'never'}
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}


function StatCard({ label, value, sub, icon, accent }: {
    label: string; value: number; sub?: string; icon: string; accent?: boolean;
}) {
    return (
        <div className={`rounded-apple-lg p-5 ${accent ? 'bg-gm-green-50 border border-gm-green-200' : 'bg-white shadow-apple-card'}`}>
            <div className="flex items-center gap-2 mb-2">
                <span className="text-base text-apple-gray-400">{icon}</span>
                <span className="text-xs text-apple-gray-400 uppercase tracking-wide">{label}</span>
            </div>
            <p className={`text-2xl font-bold ${accent ? 'text-gm-green-600' : 'text-apple-gray-800'}`}>
                {value.toLocaleString()}
            </p>
            {sub && <p className="text-xs text-apple-gray-400 mt-0.5">{sub}</p>}
        </div>
    );
}

function timeAgo(iso: string): string {
    const diff = Date.now() - new Date(iso).getTime();
    const sec = Math.floor(diff / 1000);
    if (sec < 60) return `${sec}s ago`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hrs = Math.floor(min / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
}
