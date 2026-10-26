'use client';

import { useState, useEffect, useCallback } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import {
    apiListSensors, apiGetSensorDataAdvanced, apiExportSensorData,
    apiListWavFiles, apiDownloadWav, apiDownloadWavBundle, apiCountWavFiles,
    apiUpdateSensor, SensorInfo, SensorDataResponse, WavFileInfo, WavCountInfo,
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

function formatBytes(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
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
    const [exportRange, setExportRange] = useState<string>('30d');
    const [exportFormat, setExportFormat] = useState<'csv' | 'wav' | 'both'>('csv');
    const [showExportModal, setShowExportModal] = useState(false);
    const [wavFiles, setWavFiles] = useState<WavFileInfo[]>([]);
    const [wavLoading, setWavLoading] = useState(false);
    const [downloadingWavId, setDownloadingWavId] = useState<string | null>(null);
    const [bundleLoading, setBundleLoading] = useState(false);
    const [wavFromDate, setWavFromDate] = useState(formatDate(new Date(Date.now() - 7 * 86400000)));
    const [wavToDate, setWavToDate] = useState(formatDate(new Date()));
    const [wavCount, setWavCount] = useState<WavCountInfo | null>(null);

    const loadWavFiles = useCallback(async () => {
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
    }, [sensorId, wavFromDate, wavToDate]);

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

    // Load WAV files when date range changes
    useEffect(() => {
        if (sensorId) loadWavFiles();
    }, [sensorId, loadWavFiles]);

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
            const tasks: Promise<void>[] = [];
            if (exportFormat === 'csv' || exportFormat === 'both') {
                tasks.push(apiExportSensorData(sensorId, exportRange));
            }
            if (exportFormat === 'wav' || exportFormat === 'both') {
                // Use bundle download with time range for WAV
                const fromIso = new Date(wavFromDate + 'T00:00:00Z').toISOString();
                const toIso = new Date(wavToDate + 'T23:59:59Z').toISOString();
                tasks.push(apiDownloadWavBundle(sensorId, fromIso, toIso));
            }
            await Promise.all(tasks);
            setExportStatus('done');
            setTimeout(() => { setExportStatus('idle'); setShowExportModal(false); }, 1500);
        } catch {
            setExportStatus('error');
            setTimeout(() => setExportStatus('idle'), 3000);
        }
    };

    const toggleSmsAlerts = async () => {
        if (!sensor) return;
        const newValue = !sensor.sms_alerts_enabled;
        try {
            const updated = await apiUpdateSensor(sensor.id, { sms_alerts_enabled: newValue });
            setSensor(updated);
        } catch (err) {
            console.error('Failed to update SMS alerts:', err);
            alert('Failed to update settings');
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

                {/* Action Buttons */}
                <div className="flex flex-col sm:flex-row items-center gap-3">
                    {/* SMS Alerts Toggle */}
                    <button
                        onClick={toggleSmsAlerts}
                        className={`flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-2xl transition-all border shrink-0 ${
                            sensor.sms_alerts_enabled
                                ? 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100'
                                : 'bg-gray-50 text-gray-500 border-gray-200 hover:bg-gray-100'
                        }`}
                        title="SMS Warnungen bei Elektroden-Abfall"
                    >
                        <span className="text-base">📱</span>
                        {sensor.sms_alerts_enabled ? 'SMS aktiv' : 'SMS stumm'}
                    </button>

                    {/* Export Button */}
                    <button
                        onClick={() => setShowExportModal(true)}
                        className="flex items-center gap-2 px-5 py-2.5 text-sm font-medium rounded-2xl bg-emerald-50 text-emerald-700 hover:bg-emerald-100 active:scale-[0.97] transition-all border border-emerald-200/50 shadow-sm shrink-0"
                    >
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                            <polyline points="7 10 12 15 17 10" />
                            <line x1="12" y1="15" x2="12" y2="3" />
                        </svg>
                        Forschungsdaten-Sicherung
                    </button>
                </div>
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

            {/* WAV Files Section */}
            <div className="glass-card p-5">
                <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-2">
                        <span className="text-lg">🎵</span>
                        <h2 className="text-sm font-semibold text-gray-700">WAV-Aufnahmen</h2>
                    </div>
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
                            max={formatDate(new Date())}
                            className="px-3 py-1.5 rounded-lg bg-white/80 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                        />
                    </div>

                    <div className="flex items-center gap-2 ml-auto">
                        {/* Count + Size Info */}
                        {wavCount && !wavLoading && (
                            <span className="text-xs text-gray-400">
                                {wavCount.count.toLocaleString()} Datei{wavCount.count !== 1 ? 'en' : ''}
                                {wavCount.total_bytes > 0 && (
                                    <> · {formatBytes(wavCount.total_bytes)}</>
                                )}
                            </span>
                        )}

                        {/* Bundle Download */}
                        {wavFiles.length > 0 && (
                            <button
                                onClick={async () => {
                                    setBundleLoading(true);
                                    try {
                                        const fromIso = new Date(wavFromDate + 'T00:00:00Z').toISOString();
                                        const toIso = new Date(wavToDate + 'T23:59:59Z').toISOString();
                                        await apiDownloadWavBundle(sensorId, fromIso, toIso);
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
                    <div className="space-y-2">
                        {[1, 2, 3].map(i => (
                            <div key={i} className="animate-pulse h-12 bg-black/[0.03] rounded-xl" />
                        ))}
                    </div>
                ) : wavFiles.length === 0 ? (
                    <div className="py-8 text-center text-gray-400 text-sm">
                        Keine WAV-Dateien im gewählten Zeitraum
                    </div>
                ) : (
                    <div className="space-y-2 max-h-96 overflow-y-auto">
                        {wavFiles.map(wav => {
                            const startDate = new Date(wav.started_at);
                            const sizeLabel = formatBytes(wav.file_size_bytes);
                            const isDownloading = downloadingWavId === wav.id;

                            return (
                                <div
                                    key={wav.id}
                                    className="flex items-center justify-between px-4 py-3 rounded-xl bg-white/60 border border-black/[0.04] hover:border-emerald-200/60 transition-all group"
                                >
                                    <div className="flex items-center gap-3 min-w-0">
                                        <div className="w-8 h-8 rounded-lg bg-emerald-50 text-emerald-600 flex items-center justify-center text-xs font-bold shrink-0">
                                            WAV
                                        </div>
                                        <div className="min-w-0">
                                            <div className="text-sm font-medium text-gray-700 truncate">
                                                {startDate.toLocaleDateString('de-CH', { day: '2-digit', month: 'short', year: 'numeric' })}
                                                {' · '}
                                                {startDate.toLocaleTimeString('de-CH', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                                            </div>
                                            <div className="text-xs text-gray-400 flex gap-2">
                                                <span>{wav.duration_seconds.toFixed(1)}s</span>
                                                <span>·</span>
                                                <span>{wav.sample_rate} Hz</span>
                                                <span>·</span>
                                                <span>{sizeLabel}</span>
                                            </div>
                                        </div>
                                    </div>
                                    <button
                                        onClick={async () => {
                                            setDownloadingWavId(wav.id);
                                            try {
                                                await apiDownloadWav(wav.id);
                                            } catch (err) {
                                                console.error('WAV download failed:', err);
                                            } finally {
                                                setDownloadingWavId(null);
                                            }
                                        }}
                                        disabled={isDownloading}
                                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-emerald-50 text-emerald-700 hover:bg-emerald-100 transition-all disabled:opacity-50 border border-emerald-200/50 opacity-0 group-hover:opacity-100 shrink-0"
                                    >
                                        {isDownloading ? (
                                            'Laden…'
                                        ) : (
                                            <>
                                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                                    <polyline points="7 10 12 15 17 10" />
                                                    <line x1="12" y1="15" x2="12" y2="3" />
                                                </svg>
                                                Download
                                            </>
                                        )}
                                    </button>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>

            {/* ── Export Modal (Apple HIG Style) ── */}
            {showExportModal && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
                    {/* Backdrop */}
                    <div
                        className="absolute inset-0 bg-black/25 backdrop-blur-md"
                        onClick={() => { if (exportStatus === 'idle') setShowExportModal(false); }}
                    />

                    {/* Modal */}
                    <div className="relative w-full max-w-md bg-white/85 backdrop-blur-2xl rounded-3xl shadow-2xl border border-white/60 overflow-hidden animate-in fade-in zoom-in-95 duration-200">
                        {/* Header */}
                        <div className="px-6 pt-6 pb-4">
                            <div className="flex items-center justify-between">
                                <h2 className="text-xl font-bold text-gray-800 tracking-tight">Forschungsdaten-Sicherung</h2>
                                <button
                                    onClick={() => { if (exportStatus === 'idle') setShowExportModal(false); }}
                                    className="w-8 h-8 rounded-full bg-black/[0.06] flex items-center justify-center text-gray-400 hover:text-gray-600 hover:bg-black/[0.1] transition-all"
                                >
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                                        <path d="M18 6L6 18M6 6l12 12" />
                                    </svg>
                                </button>
                            </div>
                            <p className="text-sm text-gray-400 mt-1">Wähle Zeitraum und Format für den Export</p>
                        </div>

                        {/* Time Range */}
                        <div className="px-6 pb-4">
                            <label className="block text-[11px] text-gray-400 uppercase tracking-wider font-semibold mb-2">Zeitraum</label>
                            <div className="flex gap-1.5 p-1 bg-black/[0.04] rounded-2xl">
                                {[
                                    { value: '24h', label: '24 Std.' },
                                    { value: '7d', label: '7 Tage' },
                                    { value: '30d', label: '30 Tage' },
                                    { value: 'all', label: 'Alles' },
                                ].map(opt => (
                                    <button
                                        key={opt.value}
                                        onClick={() => setExportRange(opt.value)}
                                        className={`flex-1 py-2 text-xs font-semibold rounded-xl transition-all duration-200 ${
                                            exportRange === opt.value
                                                ? 'bg-white text-gray-800 shadow-sm'
                                                : 'text-gray-500 hover:text-gray-700'
                                        }`}
                                    >
                                        {opt.label}
                                    </button>
                                ))}
                            </div>
                        </div>

                        {/* Format */}
                        <div className="px-6 pb-5">
                            <label className="block text-[11px] text-gray-400 uppercase tracking-wider font-semibold mb-2">Format</label>
                            <div className="grid grid-cols-3 gap-2">
                                {([
                                    { value: 'csv' as const, icon: '📊', label: 'CSV', desc: 'Tabellarisch' },
                                    { value: 'wav' as const, icon: '🎵', label: 'WAV', desc: 'Biosignale' },
                                    { value: 'both' as const, icon: '📦', label: 'Beides', desc: 'CSV + WAV' },
                                ]).map(fmt => (
                                    <button
                                        key={fmt.value}
                                        onClick={() => setExportFormat(fmt.value)}
                                        className={`flex flex-col items-center p-3 rounded-2xl border transition-all duration-200 ${
                                            exportFormat === fmt.value
                                                ? 'bg-emerald-50 border-emerald-300 ring-1 ring-emerald-300/50 shadow-sm'
                                                : 'bg-white/60 border-black/[0.06] hover:border-gray-300'
                                        }`}
                                    >
                                        <span className="text-xl mb-1">{fmt.icon}</span>
                                        <span className={`text-xs font-semibold ${exportFormat === fmt.value ? 'text-emerald-700' : 'text-gray-700'}`}>{fmt.label}</span>
                                        <span className="text-[10px] text-gray-400 mt-0.5">{fmt.desc}</span>
                                    </button>
                                ))}
                            </div>
                            {exportFormat !== 'csv' && wavFiles.length === 0 && (
                                <p className="mt-2 text-xs text-amber-600 bg-amber-50 rounded-xl px-3 py-2 border border-amber-200/50">
                                    Keine WAV-Dateien für diesen Sensor vorhanden
                                </p>
                            )}
                            {exportFormat !== 'csv' && wavFiles.length > 0 && (
                                <div className="mt-3">
                                    <label className="block text-[11px] text-gray-400 uppercase tracking-wider font-semibold mb-2">WAV-Zeitraum</label>
                                    <div className="flex items-center gap-2">
                                        <input
                                            type="date"
                                            value={wavFromDate}
                                            onChange={e => setWavFromDate(e.target.value)}
                                            className="flex-1 px-3 py-2 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                                        />
                                        <span className="text-xs text-gray-400">bis</span>
                                        <input
                                            type="date"
                                            value={wavToDate}
                                            onChange={e => setWavToDate(e.target.value)}
                                            max={formatDate(new Date())}
                                            className="flex-1 px-3 py-2 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                                        />
                                    </div>
                                </div>
                            )}
                        </div>

                        {/* Divider */}
                        <div className="border-t border-black/[0.06]" />

                        {/* Action */}
                        <div className="p-4 flex gap-3">
                            <button
                                onClick={() => { if (exportStatus === 'idle') setShowExportModal(false); }}
                                disabled={exportStatus === 'loading'}
                                className="flex-1 py-3 text-sm font-semibold text-gray-600 bg-black/[0.04] rounded-2xl hover:bg-black/[0.07] active:scale-[0.98] transition-all disabled:opacity-50"
                            >
                                Abbrechen
                            </button>
                            <button
                                onClick={handleExport}
                                disabled={exportStatus !== 'idle' || (exportFormat !== 'csv' && wavFiles.length === 0)}
                                className="flex-1 py-3 text-sm font-semibold text-white bg-gradient-to-b from-emerald-500 to-emerald-600 rounded-2xl hover:from-emerald-600 hover:to-emerald-700 active:scale-[0.98] transition-all disabled:opacity-50 shadow-sm flex items-center justify-center gap-2"
                            >
                                {exportStatus === 'idle' && (
                                    <>
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                            <polyline points="7 10 12 15 17 10" />
                                            <line x1="12" y1="15" x2="12" y2="3" />
                                        </svg>
                                        Exportieren
                                    </>
                                )}
                                {exportStatus === 'loading' && (
                                    <span className="flex items-center gap-2">
                                        <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                        Exportiere…
                                    </span>
                                )}
                                {exportStatus === 'done' && '✓ Fertig'}
                                {exportStatus === 'error' && '✗ Fehler – erneut versuchen'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
