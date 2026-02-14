"use client";

import React, { useState, useEffect, useCallback } from 'react';
import LiveChart from './LiveChart';

interface MetricValue {
    metric_key: string;
    value: number | null;
    unit: string;
    timestamp: string | null;
}

interface LiveDataSectionProps {
    speciesId: number | string;
}

// Canonical metrics - always show these in selector
const CANONICAL_METRICS = [
    { key: "air_temperature_c", label: "Temperature", unit: "°C" },
    { key: "rel_humidity_pct", label: "Humidity", unit: "%" },
    { key: "light_ppfd_umol_m2_s", label: "Light (PPFD)", unit: "µmol/m²/s" },
    { key: "soil_moisture_vwc_pct", label: "Soil Moisture", unit: "%" },
    { key: "soil_ph", label: "Soil pH", unit: "pH" },
    { key: "bioelectric_voltage_mv", label: "Bioelectric", unit: "mV" },
];

const LiveDataSection: React.FC<LiveDataSectionProps> = ({ speciesId }) => {
    // Accordion state
    const [isExpanded, setIsExpanded] = useState<boolean>(false);

    // Data state
    const [latestValues, setLatestValues] = useState<MetricValue[]>([]);
    const [selectedMetric, setSelectedMetric] = useState<string>("air_temperature_c");
    const [timeRange, setTimeRange] = useState<string>("24h");
    const [chartData, setChartData] = useState<any[]>([]);
    const [loading, setLoading] = useState<boolean>(false);
    const [initLoading, setInitLoading] = useState<boolean>(false);
    const [error, setError] = useState<string>("");

    const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

    // Fetch Latest Logic
    const fetchLatest = useCallback(async () => {
        setInitLoading(true);
        try {
            const res = await fetch(`${API_BASE}/species/${speciesId}/live/latest`);
            if (!res.ok) throw new Error('Failed to load live data');
            const data = await res.json();
            if (data.latest) {
                setLatestValues(data.latest);
            }
        } catch (err) {
            console.error(err);
            setError("Could not connect to sensor data.");
        } finally {
            setInitLoading(false);
        }
    }, [speciesId, API_BASE]);

    // Fetch Chart Data
    const fetchHistory = useCallback(async () => {
        if (!selectedMetric) return;
        setLoading(true);
        try {
            const res = await fetch(`${API_BASE}/species/${speciesId}/live/timeseries?metric_key=${selectedMetric}&range=${timeRange}`);
            if (!res.ok) throw new Error('Failed to load chart data');
            const data = await res.json();

            const points = data.points.map((pt: any) => ({
                timestamp: pt[0],
                value: pt[1]
            }));
            setChartData(points);
        } catch (err) {
            console.error(err);
            setChartData([]);
        } finally {
            setLoading(false);
        }
    }, [speciesId, selectedMetric, timeRange, API_BASE]);

    // Load data when expanded
    useEffect(() => {
        if (isExpanded) {
            fetchLatest();
        }
    }, [isExpanded, fetchLatest]);

    // Fetch chart when metric/range changes
    useEffect(() => {
        if (isExpanded && selectedMetric) {
            fetchHistory();
        }
    }, [isExpanded, selectedMetric, timeRange, fetchHistory]);

    // Polling when expanded
    useEffect(() => {
        if (!isExpanded) return;
        const interval = setInterval(fetchLatest, 30000);
        return () => clearInterval(interval);
    }, [isExpanded, fetchLatest]);

    const handleDownload = () => {
        const rangeHours = timeRange === "1h" ? 1 : timeRange === "6h" ? 6 : timeRange === "24h" ? 24 : timeRange === "72h" ? 72 : 168;
        const fromDate = new Date(Date.now() - rangeHours * 3600000).toISOString();
        const toDate = new Date().toISOString();

        // Use WAV for bioelectric signals, CSV for others
        if (selectedMetric === 'bioelectric_voltage_mv') {
            window.open(`${API_BASE}/species/${speciesId}/live/download/wav?from_date=${fromDate}&to_date=${toDate}`, '_blank');
        } else {
            window.open(`${API_BASE}/species/${speciesId}/live/download?from=${fromDate}&to=${toDate}`, '_blank');
        }
    };

    // Format metric keys for display
    const formatLabel = (key: string) => {
        const metric = CANONICAL_METRICS.find(m => m.key === key);
        return metric?.label || key.replace(/_/g, ' ');
    };

    // Get current metric info (from latest or canonical)
    const getCurrentMetricValue = (key: string): MetricValue | undefined => {
        return latestValues.find(m => m.metric_key === key);
    };

    const currentMetricInfo = getCurrentMetricValue(selectedMetric);
    const hasData = currentMetricInfo?.value !== null && currentMetricInfo?.value !== undefined;

    return (
        <div className="mt-8 bg-white border border-gray-200 rounded-2xl overflow-hidden shadow-sm">
            {/* Accordion Header */}
            <button
                onClick={() => setIsExpanded(!isExpanded)}
                className="w-full px-6 py-4 flex items-center justify-between hover:bg-gray-50 transition-colors"
            >
                <div className="flex items-center gap-3">
                    <div className={`w-2 h-2 rounded-full ${latestValues.some(v => v.value !== null) ? 'bg-emerald-500' : 'bg-gray-300'}`} />
                    <h3 className="text-lg font-semibold text-gray-800">Live Data</h3>
                    {!isExpanded && latestValues.some(v => v.value !== null) && (
                        <span className="text-sm text-gray-500 ml-2">
                            {latestValues.filter(v => v.value !== null).length} active sensors
                        </span>
                    )}
                </div>
                <svg
                    className={`w-5 h-5 text-gray-400 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
                </svg>
            </button>

            {/* Accordion Content */}
            {isExpanded && (
                <div className="px-6 pb-6 border-t border-gray-100 animate-in slide-in-from-top-2 duration-200">
                    {initLoading ? (
                        <div className="py-12 text-center text-gray-400 animate-pulse">Connecting to sensors...</div>
                    ) : error ? (
                        <div className="py-8 text-center text-red-500">{error}</div>
                    ) : (
                        <div className="space-y-6 pt-4">
                            {/* Metric Cards Grid */}
                            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
                                {CANONICAL_METRICS.map(metric => {
                                    const val = getCurrentMetricValue(metric.key);
                                    const hasValue = val?.value !== null && val?.value !== undefined;

                                    return (
                                        <button
                                            key={metric.key}
                                            onClick={() => setSelectedMetric(metric.key)}
                                            className={`p-3 rounded-xl border text-left transition-all ${selectedMetric === metric.key
                                                ? 'bg-emerald-50 border-emerald-500 ring-1 ring-emerald-500'
                                                : hasValue
                                                    ? 'bg-white border-gray-200 hover:border-emerald-200 hover:shadow-sm'
                                                    : 'bg-gray-50 border-gray-100 hover:border-gray-200'
                                                }`}
                                        >
                                            <div className="text-xs text-gray-500 font-medium uppercase tracking-wider mb-1 truncate">
                                                {metric.label}
                                            </div>
                                            <div className="flex items-baseline gap-1">
                                                {hasValue ? (
                                                    <>
                                                        <span className="text-xl font-bold text-gray-800">
                                                            {val!.value!.toFixed(1)}
                                                        </span>
                                                        <span className="text-xs text-gray-500">{metric.unit}</span>
                                                    </>
                                                ) : (
                                                    <span className="text-sm text-gray-400">No data</span>
                                                )}
                                            </div>
                                        </button>
                                    );
                                })}
                            </div>

                            {/* Chart Area */}
                            <div className="bg-gray-50 rounded-xl p-5">
                                <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-4">
                                    <div>
                                        <h4 className="text-base font-semibold text-gray-800 flex items-center gap-2">
                                            {formatLabel(selectedMetric)} History
                                            {selectedMetric === 'bioelectric_voltage_mv' && (
                                                <span className="px-2 py-0.5 bg-purple-100 text-purple-700 text-xs rounded-full">Signal</span>
                                            )}
                                        </h4>
                                    </div>

                                    <div className="flex items-center gap-1 bg-white border border-gray-200 p-1 rounded-lg">
                                        {["1h", "6h", "24h", "72h", "7d"].map(r => (
                                            <button
                                                key={r}
                                                onClick={() => setTimeRange(r)}
                                                className={`px-3 py-1 rounded-md text-xs font-medium transition-all ${timeRange === r
                                                    ? 'bg-emerald-100 text-emerald-700'
                                                    : 'text-gray-500 hover:text-gray-700'
                                                    }`}
                                            >
                                                {r}
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                <div className="h-64 w-full">
                                    {loading ? (
                                        <div className="h-full flex items-center justify-center text-gray-400">Loading...</div>
                                    ) : chartData.length === 0 || !hasData ? (
                                        <div className="h-full flex items-center justify-center text-gray-400">
                                            No live data received yet for this metric.
                                        </div>
                                    ) : (
                                        <LiveChart
                                            data={chartData}
                                            metricKey={formatLabel(selectedMetric)}
                                            unit={currentMetricInfo?.unit || ""}
                                            color={selectedMetric === 'bioelectric_voltage_mv' ? '#8b5cf6' : '#10b981'}
                                        />
                                    )}
                                </div>

                                <div className="mt-3 flex justify-end">
                                    <button
                                        onClick={handleDownload}
                                        disabled={chartData.length === 0}
                                        className={`text-sm flex items-center gap-1 transition-colors ${chartData.length === 0
                                            ? 'text-gray-300 cursor-not-allowed'
                                            : 'text-gray-500 hover:text-emerald-600'
                                            }`}
                                    >
                                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                                        </svg>
                                        {selectedMetric === 'bioelectric_voltage_mv' ? 'Export WAV' : 'Export CSV'}
                                    </button>
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default LiveDataSection;
