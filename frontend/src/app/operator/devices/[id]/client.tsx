'use client';

import { useEffect, useState, useRef } from 'react';
import { fetchDeviceLive, getDeviceDownloadUrl, DeviceLiveData } from '@/lib/api';
import Link from 'next/link';

export default function OperatorDeviceLiveClient({ id }: { id: string }) {
    const [liveData, setLiveData] = useState<DeviceLiveData | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

    // Date range for download
    const [fromDate, setFromDate] = useState<string>(new Date(Date.now() - 86400000).toISOString().split('T')[0]);
    const [toDate, setToDate] = useState<string>(new Date().toISOString().split('T')[0]);

    // Polling
    useEffect(() => {
        let isMounted = true;
        const poll = async () => {
            try {
                const data = await fetchDeviceLive(id);
                if (isMounted) {
                    setLiveData(data);
                    setLastUpdated(new Date());
                    setError(null);
                }
            } catch (err: any) {
                if (isMounted) setError(err.message);
            }
        };

        poll();
        const interval = setInterval(poll, 2000);
        return () => {
            isMounted = false;
            clearInterval(interval);
        };
    }, [id]);

    const handleDownload = (metric: 'env' | 'signal') => {
        const url = getDeviceDownloadUrl(
            id,
            metric,
            fromDate ? new Date(fromDate) : undefined,
            toDate ? new Date(toDate + 'T23:59:59') : undefined
        );
        window.open(url, '_blank');
    };

    if (!liveData && !error) return <div className="p-8 text-center text-gray-500">Connecting to device...</div>;

    return (
        <main className="max-w-7xl mx-auto px-6 py-8">
            <div className="flex items-center justify-between mb-8">
                <div className="flex items-center gap-4">
                    <Link href="/operator/greenhouses" className="text-sm text-gray-500 hover:text-green-600 transition-colors">
                        ← Back
                    </Link>
                    <h1 className="text-2xl font-bold text-gray-900">Device Monitor</h1>
                    <span className="font-mono text-sm bg-gray-100 px-2 py-1 rounded text-gray-600">
                        {id.split('-')[0]}...
                    </span>
                </div>
                <div className="flex items-center gap-2 text-sm text-gray-500">
                    <span className={`w-2 h-2 rounded-full ${error ? 'bg-red-500' : 'bg-green-500 animate-pulse'}`} />
                    {error ? 'Connection Error' : 'Live Stream Active'}
                </div>
            </div>

            {error && (
                <div className="bg-red-50 text-red-700 p-4 rounded-xl mb-6 border border-red-200">
                    {error}
                </div>
            )}

            {/* Live Data Cards */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
                {liveData && Object.entries(liveData.sensors).map(([sensorId, data]) => (
                    <div key={sensorId} className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
                        <div className="text-sm text-gray-500 uppercase tracking-wider font-medium mb-1">
                            {data.kind}
                        </div>
                        <div className="flex items-baseline gap-1">
                            <span className="text-3xl font-bold text-gray-900">
                                {typeof data.value === 'number' ? data.value.toFixed(2) : data.value}
                            </span>
                            <span className="text-gray-500 font-medium">{data.unit}</span>
                        </div>
                        <div className="text-xs text-gray-400 mt-2">
                            Updated: {new Date(data.time).toLocaleTimeString()}
                        </div>
                    </div>
                ))}
                {(!liveData || Object.keys(liveData.sensors).length === 0) && !error && (
                    <div className="col-span-full text-center py-8 text-gray-400 bg-gray-50 rounded-xl border border-dashed">
                        No active sensor data received yet.
                    </div>
                )}
            </div>

            {/* Historical Data & Download */}
            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
                <h2 className="text-lg font-bold text-gray-900 mb-4">Historical Data</h2>

                <div className="flex flex-wrap items-end gap-4 mb-6">
                    <div>
                        <label className="block text-xs font-medium text-gray-700 mb-1">From</label>
                        <input
                            type="date"
                            value={fromDate}
                            onChange={(e) => setFromDate(e.target.value)}
                            className="text-sm border-gray-300 rounded-md shadow-sm focus:border-green-500 focus:ring-green-500"
                        />
                    </div>
                    <div>
                        <label className="block text-xs font-medium text-gray-700 mb-1">To</label>
                        <input
                            type="date"
                            value={toDate}
                            onChange={(e) => setToDate(e.target.value)}
                            className="text-sm border-gray-300 rounded-md shadow-sm focus:border-green-500 focus:ring-green-500"
                        />
                    </div>
                    <div className="flex gap-2">
                        <button
                            onClick={() => handleDownload('env')}
                            className="px-4 py-2 bg-white border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors flex items-center gap-2"
                        >
                            <span>⬇️</span> Download CSV (Env)
                        </button>
                        <button
                            onClick={() => handleDownload('signal')}
                            className="px-4 py-2 bg-white border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors flex items-center gap-2"
                        >
                            <span>⬇️</span> Download CSV (Signal)
                        </button>
                    </div>
                </div>

                <div className="h-64 bg-gray-50 rounded-lg flex items-center justify-center text-gray-400 text-sm border border-gray-100">
                    Historical charts require implementing specific API endpoints for aggregated time-series data.
                    <br />Currently available via CSV download.
                </div>
            </div>
        </main>
    );
}
