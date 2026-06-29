'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import {
    apiListSensors, apiGetSensorData, apiGetSensorDataAdvanced,
    apiExportSensorData, apiDeleteSensor, apiListWavFiles, apiDownloadWav,
    apiDownloadWavBundle, apiCountWavFiles, createSensorWebSocket,
    SensorInfo, SensorDataResponse, WavFileInfo, WavCountInfo,
} from '@/lib/api';
import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Brush
} from 'recharts';
import PairSensorDialog from './PairSensorDialog';

type TimeRange = 'live' | '1h' | '24h' | '7d' | '30d';

const KIND_CONFIG: Record<string, { label: string; unit: string; color: string; icon: string }> = {
    temperature: { label: 'Temperatur', unit: '°C', color: '#ef4444', icon: '🌡' },
    humidity: { label: 'Luftfeuchtigkeit', unit: '%', color: '#3b82f6', icon: '💧' },
    soil_moisture: { label: 'Bodenfeuchtigkeit', unit: '%', color: '#a855f7', icon: '🌱' },
    bioelectric: { label: 'Bioelektrisches Signal', unit: 'mV', color: '#10b981', icon: '⚡' },
    bio_signal: { label: 'Bioelektrisches Signal', unit: 'mV', color: '#10b981', icon: '⚡' },
};

const BIO_SIGNAL_KINDS = new Set(['bioelectric', 'bio_signal']);
const ELECTRODE_RAIL_HIGH = 3295;
const ELECTRODE_RAIL_LOW = 5;
const ELECTRODE_MIN_FLAT_POINTS = 10;

type ElectrodeStatus = 'ok' | 'rail_high' | 'rail_low';

function detectElectrodeDisconnect(kind: string, data: { value: number }[]): ElectrodeStatus {
    if (!BIO_SIGNAL_KINDS.has(kind) || data.length < ELECTRODE_MIN_FLAT_POINTS) return 'ok';
    const tail = data.slice(-ELECTRODE_MIN_FLAT_POINTS);
    if (tail.every(p => p.value >= ELECTRODE_RAIL_HIGH)) return 'rail_high';
    if (tail.every(p => p.value <= ELECTRODE_RAIL_LOW)) return 'rail_low';
    return 'ok';
}

function formatDateStr(d: Date): string {
    return d.toISOString().split('T')[0];
}

function formatBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

type ExportStatus = 'idle' | 'loading' | 'zipping' | 'done' | 'error';

export default function SensorsPage() {
    const [sensors, setSensors] = useState<SensorInfo[]>([]);
    const [loading, setLoading] = useState(true);
    const [selectedSensor, setSelectedSensor] = useState<string | null>(null);
    const [sensorData, setSensorData] = useState<SensorDataResponse[]>([]);
    const [loadingData, setLoadingData] = useState(false);
    const [timeRange, setTimeRange] = useState<TimeRange>('24h');
    const hasInitialData = useRef(false);
    const [brushRanges, setBrushRanges] = useState<Record<string, { startIndex: number; endIndex: number }>>({});
    const [exportStatus, setExportStatus] = useState<ExportStatus>('idle');
    const [deletingSensorId, setDeletingSensorId] = useState<string | null>(null);
    const [isDeleting, setIsDeleting] = useState(false);
    const [isPairDialogOpen, setIsPairDialogOpen] = useState(false);
    const [wsConnected, setWsConnected] = useState(false);
    const [wavFiles, setWavFiles] = useState<WavFileInfo[]>([]);
    const [wavLoading, setWavLoading] = useState(false);
    const [downloadingWavId, setDownloadingWavId] = useState<string | null>(null);
    const [bundleLoading, setBundleLoading] = useState(false);
    const [wavFromDate, setWavFromDate] = useState(formatDateStr(new Date(Date.now() - 7 * 86400000)));
    const [wavToDate, setWavToDate] = useState(formatDateStr(new Date()));
    const [wavCount, setWavCount] = useState<WavCountInfo | null>(null);
    const wsCleanupRef = useRef<(() => void) | null>(null);
    const [realtimeActive, setRealtimeActive] = useState(false);
    const [realtimeRemaining, setRealtimeRemaining] = useState(0);
    const realtimeTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const realtimeExpiryRef = useRef<number>(0);

    const REALTIME_DURATION_S = 300; // 5 minutes

    const refreshSensors = useCallback(() => {
        apiListSensors()
            .then(setSensors)
            .catch(console.error)
            .finally(() => setLoading(false));
    }, []);

    useEffect(() => {
        refreshSensors();
    }, [refreshSensors]);

    const loadSensorData = useCallback(async (sensorId: string, range: TimeRange, silent = false) => {
        if (!silent) {
            setLoadingData(true);
            hasInitialData.current = false;
        }
        try {
            let data: SensorDataResponse[];
            if (range === 'live') {
                // Raw data for last 5 minutes — max resolution (~1600 points at 5Hz)
                data = await apiGetSensorDataAdvanced(sensorId, { range: '5m', resolution: 'raw' });
            } else {
                data = await apiGetSensorData(sensorId, range);
            }
            setSensorData(data);
            hasInitialData.current = true;
        } catch (err) {
            console.error('Failed to load sensor data:', err);
            if (!silent) setSensorData([]);
        } finally {
            if (!silent) setLoadingData(false);
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
        setBrushRanges({});
    };

    const handleRangeChange = (range: TimeRange) => {
        setTimeRange(range);
        setBrushRanges({});
        if (selectedSensor) {
            loadSensorData(selectedSensor, range);
        }
    };

    const formatTime = (t: string) => {
        const d = new Date(t);
        const h = d.getHours().toString().padStart(2, '0');
        const m = d.getMinutes().toString().padStart(2, '0');
        return `${h}:${m}`;
    };

    const formatDate = (t: string) => {
        const d = new Date(t);
        const day = d.getDate().toString().padStart(2, '0');
        const month = (d.getMonth() + 1).toString().padStart(2, '0');
        return `${day}.${month}`;
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

    // Check if any chart is zoomed
    const isZoomed = Object.keys(brushRanges).length > 0;

    // Realtime mode: start/stop handlers
    const stopRealtime = useCallback(() => {
        setRealtimeActive(false);
        setRealtimeRemaining(0);
        realtimeExpiryRef.current = 0;
        if (realtimeTimerRef.current) {
            clearInterval(realtimeTimerRef.current);
            realtimeTimerRef.current = null;
        }
        if (wsCleanupRef.current) {
            wsCleanupRef.current();
            wsCleanupRef.current = null;
            setWsConnected(false);
        }
    }, []);

    const startRealtime = useCallback(() => {
        setRealtimeActive(true);
        const expiry = Date.now() + REALTIME_DURATION_S * 1000;
        realtimeExpiryRef.current = expiry;
        setRealtimeRemaining(REALTIME_DURATION_S);

        // Countdown timer
        if (realtimeTimerRef.current) clearInterval(realtimeTimerRef.current);
        realtimeTimerRef.current = setInterval(() => {
            const left = Math.max(0, Math.ceil((realtimeExpiryRef.current - Date.now()) / 1000));
            setRealtimeRemaining(left);
            if (left <= 0) {
                stopRealtime();
            }
        }, 1000);
    }, [stopRealtime]);

    // Stop realtime when leaving live mode or deselecting sensor
    useEffect(() => {
        if (timeRange !== 'live' || !selectedSensor) {
            stopRealtime();
        }
    }, [timeRange, selectedSensor, stopRealtime]);

    // WebSocket: only active during Realtime mode
    useEffect(() => {
        // Clean up any previous WebSocket
        if (wsCleanupRef.current) {
            wsCleanupRef.current();
            wsCleanupRef.current = null;
            setWsConnected(false);
        }

        if (!selectedSensor || timeRange !== 'live' || !realtimeActive || isZoomed) return;

        const cleanup = createSensorWebSocket(
            selectedSensor,
            (msg) => {
                if (msg.event !== 'live_reading') return;
                setSensorData((prev) => {
                    return prev.map((series) => {
                        const relevantReadings = msg.readings.filter(r => r.kind === series.kind);
                        if (relevantReadings.length === 0) return series;

                        const newPoints = relevantReadings.map(r => ({
                            timestamp: typeof r.timestamp === 'string' ? r.timestamp : new Date(r.timestamp).toISOString(),
                            value: r.value,
                        }));

                        const merged = [...series.data, ...newPoints].slice(-300);
                        return { ...series, data: merged };
                    });
                });
            },
            (connected) => setWsConnected(connected),
        );

        wsCleanupRef.current = cleanup;
        return () => {
            cleanup();
            wsCleanupRef.current = null;
            setWsConnected(false);
        };
    }, [selectedSensor, timeRange, realtimeActive, isZoomed]);

    // Default live polling (5s) — always runs in live mode unless Realtime is active
    useEffect(() => {
        if (!selectedSensor || timeRange !== 'live' || isZoomed || realtimeActive) return;
        const interval = setInterval(() => {
            loadSensorData(selectedSensor, 'live', true);
        }, 5000);
        return () => clearInterval(interval);
    }, [selectedSensor, timeRange, loadSensorData, isZoomed, realtimeActive]);

    const formatCountdown = (seconds: number): string => {
        const m = Math.floor(seconds / 60);
        const s = seconds % 60;
        return `${m}:${s.toString().padStart(2, '0')}`;
    };

    // Load WAV files when a sensor is selected or date range changes
    const loadWavFiles = useCallback(async (sensorId: string) => {
        setWavLoading(true);
        const fromIso = new Date(wavFromDate + 'T00:00:00Z').toISOString();
        const toIso = new Date(wavToDate + 'T23:59:59Z').toISOString();
        try {
            const [files, count] = await Promise.all([
                apiListWavFiles(sensorId, { from_dt: fromIso, to_dt: toIso, limit: 10000 }),
                apiCountWavFiles(sensorId, fromIso, toIso),
            ]);
            setWavFiles(files);
            setWavCount(count);
        } catch {
            setWavFiles([]);
            setWavCount(null);
        } finally {
            setWavLoading(false);
        }
    }, [wavFromDate, wavToDate]);

    useEffect(() => {
        if (!selectedSensor) {
            setWavFiles([]);
            setWavCount(null);
            return;
        }
        loadWavFiles(selectedSensor);
    }, [selectedSensor, loadWavFiles]);

    const handleWavDownload = async (wavId: string) => {
        setDownloadingWavId(wavId);
        try {
            await apiDownloadWav(wavId);
        } catch (err) {
            console.error('WAV download failed:', err);
        } finally {
            setDownloadingWavId(null);
        }
    };

    const formatFileSize = (bytes: number): string => {
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    };

    const confirmDeleteSensor = async () => {
        if (!deletingSensorId) return;
        setIsDeleting(true);
        try {
            await apiDeleteSensor(deletingSensorId);
            setSensors(prev => prev.filter(s => s.id !== deletingSensorId));
            if (selectedSensor === deletingSensorId) {
                setSelectedSensor(null);
                setSensorData([]);
            }
            setDeletingSensorId(null);
        } catch (err) {
            console.error('Failed to delete sensor:', err);
        } finally {
            setIsDeleting(false);
        }
    };

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
        <div className="space-y-6 relative">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-gray-800 tracking-tight">Sensoren</h1>
                    <p className="text-sm text-gray-400 mt-1">ESP32-Sensormodule – klicke auf einen Sensor für Live-Daten</p>
                </div>
                <button
                    onClick={() => setIsPairDialogOpen(true)}
                    className="flex items-center justify-center gap-2 px-6 py-2.5 bg-gray-900 text-white text-sm font-medium rounded-2xl hover:bg-gray-800 hover:shadow-lg hover:-translate-y-0.5 transition-all shadow-md active:scale-95 text-center whitespace-nowrap"
                >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                    Sensor Koppeln
                </button>
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
                                    <th className="text-left px-5 py-3.5 text-[11px] font-medium text-gray-400 uppercase tracking-wider w-12"></th>
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
                                        <td className="px-5 py-3.5">
                                            <button
                                                onClick={(e) => { e.stopPropagation(); setDeletingSensorId(s.id); }}
                                                className="p-1.5 text-gray-300 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all"
                                                title="Sensor entfernen"
                                            >
                                                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                                </svg>
                                            </button>
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
                                            const electrodeStatus = detectElectrodeDisconnect(series.kind, series.data);

                                            return (
                                                <div
                                                    key={series.kind}
                                                    className={`bg-white/40 rounded-2xl p-4 border backdrop-blur-sm ${
                                                        electrodeStatus !== 'ok'
                                                            ? 'border-amber-400/40 shadow-amber-100'
                                                            : 'border-black/[0.04]'
                                                    }`}
                                                >
                                                    {/* Electrode Disconnect Warning */}
                                                    {electrodeStatus !== 'ok' && (
                                                        <div className="mb-3 flex items-center gap-2.5 px-3.5 py-2.5 bg-amber-50 border border-amber-200/60 rounded-xl animate-pulse">
                                                            <span className="text-lg flex-shrink-0">⚠️</span>
                                                            <div>
                                                                <p className="text-sm font-semibold text-amber-800">
                                                                    Elektrode abgefallen?
                                                                </p>
                                                                <p className="text-xs text-amber-600 mt-0.5">
                                                                    Signal konstant bei {electrodeStatus === 'rail_high' ? '~3300 mV (Sättigung)' : '~0 mV (kein Kontakt)'}.
                                                                    Bitte Elektroden prüfen.
                                                                </p>
                                                            </div>
                                                        </div>
                                                    )}

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
                                                        <ResponsiveContainer width="100%" height={220}>
                                                            <LineChart data={series.data}>
                                                                <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,0.04)" vertical={false} />
                                                                <XAxis
                                                                    dataKey="timestamp"
                                                                    tickFormatter={(t) => {
                                                                        if (timeRange === '1h' || timeRange === '24h' || timeRange === 'live') {
                                                                            return formatTime(t);
                                                                        }
                                                                        return formatDate(t);
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
                                                                    isAnimationActive={timeRange !== 'live'}
                                                                />
                                                                <Brush
                                                                    dataKey="timestamp"
                                                                    height={24}
                                                                    stroke={config.color}
                                                                    fill="rgba(0,0,0,0.02)"
                                                                    travellerWidth={8}
                                                                    startIndex={brushRanges[series.kind]?.startIndex}
                                                                    endIndex={brushRanges[series.kind]?.endIndex}
                                                                    onChange={(range) => {
                                                                        if (range && typeof range.startIndex === 'number' && typeof range.endIndex === 'number') {
                                                                            setBrushRanges(prev => ({
                                                                                ...prev,
                                                                                [series.kind]: { startIndex: range.startIndex!, endIndex: range.endIndex! },
                                                                            }));
                                                                        }
                                                                    }}
                                                                    tickFormatter={(t) => formatTime(t)}
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

                                {/* Live indicator + Realtime button */}
                                {timeRange === 'live' && !loadingData && sensorData.length > 0 && (
                                    <div className="mt-4 flex items-center justify-center gap-3 text-xs text-gray-400">
                                        {isZoomed ? (
                                            <>
                                                <span className="w-2 h-2 rounded-full bg-amber-500" />
                                                Zoom aktiv – Live-Aktualisierung pausiert
                                                <button
                                                    onClick={() => setBrushRanges({})}
                                                    className="ml-2 px-2 py-0.5 bg-gray-100 hover:bg-gray-200 rounded text-gray-500 transition-colors"
                                                >
                                                    Zoom zurücksetzen
                                                </button>
                                            </>
                                        ) : realtimeActive ? (
                                            <>
                                                <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.4)]" />
                                                <span className="text-emerald-600 font-medium">
                                                    Realtime {wsConnected ? 'verbunden' : 'verbindet…'}
                                                </span>
                                                <span className="font-mono text-emerald-500 bg-emerald-50 px-1.5 py-0.5 rounded">
                                                    {formatCountdown(realtimeRemaining)}
                                                </span>
                                                <button
                                                    onClick={stopRealtime}
                                                    className="px-2.5 py-1 bg-red-50 text-red-600 hover:bg-red-100 rounded-lg font-medium transition-colors border border-red-200/50"
                                                >
                                                    Stop
                                                </button>
                                            </>
                                        ) : (
                                            <>
                                                <span className="w-2 h-2 rounded-full bg-gray-400 animate-pulse" />
                                                Live – aktualisiert alle 5s
                                                <button
                                                    onClick={startRealtime}
                                                    className="ml-1 inline-flex items-center gap-1.5 px-3 py-1 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 rounded-lg font-medium transition-all duration-200 border border-emerald-200/50 hover:shadow-sm"
                                                >
                                                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                                        <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                                                    </svg>
                                                    Realtime
                                                </button>
                                            </>
                                        )}
                                    </div>
                                )}

                                {/* WAV Files Section */}
                                <div className="mt-6 pt-6 border-t border-black/[0.04]">
                                    <div className="flex items-center gap-2 mb-3">
                                        <span className="text-lg">🎵</span>
                                        <span className="text-sm font-medium text-gray-700">WAV-Dateien (380 Hz Rohdaten)</span>
                                    </div>

                                    {/* Date Range Filter */}
                                    <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3 mb-4 p-3 rounded-xl bg-black/[0.02] border border-black/[0.04]">
                                        <div className="flex items-center gap-2 flex-wrap">
                                            <label className="text-xs text-gray-500 font-medium">Von</label>
                                            <input
                                                type="date"
                                                value={wavFromDate}
                                                onChange={e => setWavFromDate(e.target.value)}
                                                className="px-3 py-1.5 rounded-lg bg-white/80 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                                            />
                                            <label className="text-xs text-gray-500 font-medium">Bis</label>
                                            <input
                                                type="date"
                                                value={wavToDate}
                                                onChange={e => setWavToDate(e.target.value)}
                                                max={formatDateStr(new Date())}
                                                className="px-3 py-1.5 rounded-lg bg-white/80 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                                            />
                                        </div>

                                        <div className="flex items-center gap-2 ml-auto">
                                            {wavCount && !wavLoading && (
                                                <span className="text-xs text-gray-400">
                                                    {wavCount.count.toLocaleString()} Datei{wavCount.count !== 1 ? 'en' : ''}
                                                    {wavCount.total_bytes > 0 && (
                                                        <> · {formatBytes(wavCount.total_bytes)}</>
                                                    )}
                                                </span>
                                            )}

                                            {wavFiles.length > 0 && selectedSensor && (
                                                <button
                                                    onClick={async () => {
                                                        setBundleLoading(true);
                                                        try {
                                                            const fromIso = new Date(wavFromDate + 'T00:00:00Z').toISOString();
                                                            const toIso = new Date(wavToDate + 'T23:59:59Z').toISOString();
                                                            await apiDownloadWavBundle(selectedSensor, fromIso, toIso);
                                                        } catch (err) {
                                                            console.error('Bundle download failed:', err);
                                                        } finally {
                                                            setBundleLoading(false);
                                                        }
                                                    }}
                                                    disabled={bundleLoading}
                                                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-emerald-50 text-emerald-700 hover:bg-emerald-100 transition-all disabled:opacity-50 border border-emerald-200/50"
                                                >
                                                    {bundleLoading ? (
                                                        <span className="flex items-center gap-1">
                                                            <span className="w-3 h-3 border-2 border-emerald-300 border-t-emerald-600 rounded-full animate-spin" />
                                                            ZIP…
                                                        </span>
                                                    ) : (
                                                        <>
                                                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                                                <polyline points="7 10 12 15 17 10" />
                                                                <line x1="12" y1="15" x2="12" y2="3" />
                                                            </svg>
                                                            Alle als ZIP
                                                        </>
                                                    )}
                                                </button>
                                            )}
                                        </div>
                                    </div>

                                    {wavLoading ? (
                                        <div className="animate-pulse h-24 bg-black/[0.03] rounded-2xl" />
                                    ) : wavFiles.length === 0 ? (
                                        <div className="py-6 text-center text-gray-400 text-sm">
                                            Keine WAV-Dateien im gewählten Zeitraum
                                        </div>
                                    ) : (
                                        <div className="bg-white/40 rounded-2xl border border-black/[0.04] overflow-hidden">
                                            <table className="w-full text-sm">
                                                <thead>
                                                    <tr className="border-b border-black/[0.04]">
                                                        <th className="text-left px-4 py-2.5 text-[11px] font-medium text-gray-400 uppercase tracking-wider">Datum</th>
                                                        <th className="text-left px-4 py-2.5 text-[11px] font-medium text-gray-400 uppercase tracking-wider">Dauer</th>
                                                        <th className="text-left px-4 py-2.5 text-[11px] font-medium text-gray-400 uppercase tracking-wider">Grösse</th>
                                                        <th className="text-right px-4 py-2.5 text-[11px] font-medium text-gray-400 uppercase tracking-wider w-24"></th>
                                                    </tr>
                                                </thead>
                                                <tbody className="divide-y divide-black/[0.03]">
                                                    {wavFiles.map((wf) => (
                                                        <tr key={wf.id} className="hover:bg-white/50 transition-colors">
                                                            <td className="px-4 py-2.5 text-gray-700">
                                                                {new Date(wf.started_at).toLocaleString('de-CH')}
                                                            </td>
                                                            <td className="px-4 py-2.5 text-gray-500">
                                                                {wf.duration_seconds.toFixed(0)}s
                                                            </td>
                                                            <td className="px-4 py-2.5 text-gray-500">
                                                                {formatFileSize(wf.file_size_bytes)}
                                                            </td>
                                                            <td className="px-4 py-2.5 text-right">
                                                                <button
                                                                    onClick={() => handleWavDownload(wf.id)}
                                                                    disabled={downloadingWavId === wf.id}
                                                                    className="inline-flex items-center gap-1.5 px-3 py-1 text-xs font-medium rounded-lg bg-emerald-50 text-emerald-700 hover:bg-emerald-100 transition-all duration-200 disabled:opacity-50 border border-emerald-200/50"
                                                                >
                                                                    {downloadingWavId === wf.id ? (
                                                                        'Lade…'
                                                                    ) : (
                                                                        <>
                                                                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                                                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                                                                <polyline points="7 10 12 15 17 10" />
                                                                                <line x1="12" y1="15" x2="12" y2="3" />
                                                                            </svg>
                                                                            WAV
                                                                        </>
                                                                    )}
                                                                </button>
                                                            </td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    )}
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* Delete Sensor Confirmation Modal */}
            {deletingSensorId && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
                    <div className="absolute inset-0 bg-black/20 backdrop-blur-sm" onClick={() => setDeletingSensorId(null)} />
                    <div className="relative bg-white/80 rounded-2xl border border-white/50 shadow-2xl backdrop-blur-xl p-6 max-w-sm w-full animate-in fade-in zoom-in duration-200">
                        <div className="w-12 h-12 rounded-full bg-red-50 text-red-500 flex items-center justify-center mb-4 mx-auto">
                            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                        </div>
                        <h3 className="text-lg font-semibold text-center text-gray-800 mb-2">Sensor entfernen?</h3>
                        <p className="text-sm text-center text-gray-500 mb-6">
                            Alle Messdaten dieses Sensors werden unwiderruflich gelöscht.
                        </p>
                        <div className="flex gap-3">
                            <button
                                onClick={() => setDeletingSensorId(null)}
                                className="flex-1 px-4 py-2.5 bg-white border border-gray-200 text-gray-700 rounded-xl text-sm font-medium hover:bg-gray-50 transition-colors"
                            >
                                Abbrechen
                            </button>
                            <button
                                onClick={confirmDeleteSensor}
                                disabled={isDeleting}
                                className="flex-1 px-4 py-2.5 bg-red-500 text-white rounded-xl text-sm font-medium hover:bg-red-600 transition-colors disabled:opacity-50"
                            >
                                {isDeleting ? 'Lösche…' : 'Löschen'}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            <PairSensorDialog
                isOpen={isPairDialogOpen}
                onClose={() => setIsPairDialogOpen(false)}
                onSuccess={() => {
                    setIsPairDialogOpen(false);
                    refreshSensors();
                }}
            />
        </div>
    );
}
