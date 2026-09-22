'use client';

import { useLocale } from 'next-intl';
import { useState, useEffect, useCallback, useRef, useMemo, Fragment } from 'react';
import {
  apiListSensors,
  apiListZones,
  Zone,
  apiGetSensorData,
  apiGetSensorDataAdvanced,
  apiExportSensorData,
  apiDeleteSensor,
  apiDownloadWavBundle,
  apiCountWavFiles,
  createSensorWebSocket,
  apiUpdateSensor,
  SensorInfo,
  SensorDataResponse,
  WavCountInfo,
} from '@/lib/api';
import SignalChart from './SignalChart';
import PairSensorDialog from './PairSensorDialog';
import DirectSensorsPanel, { useDirectDevices } from './DirectSensorsPanel';

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

function detectElectrodeDisconnect(
  kind: string,
  unit: string,
  data: { value: number }[]
): ElectrodeStatus {
  if (!BIO_SIGNAL_KINDS.has(kind) || data.length < ELECTRODE_MIN_FLAT_POINTS) return 'ok';

  const multiplier = unit === 'V' ? 1000 : 1;

  const tail = data.slice(-ELECTRODE_MIN_FLAT_POINTS);
  if (tail.every((p) => p.value * multiplier >= ELECTRODE_RAIL_HIGH)) return 'rail_high';
  if (tail.every((p) => p.value * multiplier <= ELECTRODE_RAIL_LOW)) return 'rail_low';
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

const THREE_DAYS_MS = 3 * 24 * 60 * 60 * 1000;

function isSensorArchived(s: SensorInfo): boolean {
  if (s.status === 'online') return false;

  if (s.last_seen) {
    const lastSeenTime = new Date(s.last_seen).getTime();
    if (!isNaN(lastSeenTime)) {
      return Date.now() - lastSeenTime > THREE_DAYS_MS;
    }
  }

  if (s.claimed_at) {
    const claimedTime = new Date(s.claimed_at).getTime();
    if (!isNaN(claimedTime)) {
      return Date.now() - claimedTime > THREE_DAYS_MS;
    }
  }

  return true;
}

type ExportStatus = 'idle' | 'loading' | 'zipping' | 'done' | 'error';

export default function SensorsPage() {
  const locale = useLocale();
  const [listError, setListError] = useState(false);
  const [dataError, setDataError] = useState(false);
  const [sensors, setSensors] = useState<SensorInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedSensor, setSelectedSensor] = useState<string | null>(null);
  const [zones, setZones] = useState<Zone[]>([]);
  const [selectedZone, setSelectedZone] = useState('');
  const directFeed = useDirectDevices();

  const activeSensors = useMemo(() => sensors.filter((s) => !isSensorArchived(s)), [sensors]);
  const archivedSensors = useMemo(() => sensors.filter((s) => isSensorArchived(s)), [sensors]);
  const [sensorData, setSensorData] = useState<SensorDataResponse[]>([]);
  const [loadingData, setLoadingData] = useState(false);
  const [timeRange, setTimeRange] = useState<TimeRange>('24h');
  const [electrodeStatuses, setElectrodeStatuses] = useState<Record<string, ElectrodeStatus>>({});
  const hasInitialData = useRef(false);
  const [brushRanges, setBrushRanges] = useState<
    Record<string, { startIndex: number; endIndex: number }>
  >({});
  const [exportStatus, setExportStatus] = useState<ExportStatus>('idle');
  const [deletingSensorId, setDeletingSensorId] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isUpdatingSms, setIsUpdatingSms] = useState(false);
  const [isPairDialogOpen, setIsPairDialogOpen] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);

  const [wavLoading, setWavLoading] = useState(false);

  const [bundleLoading, setBundleLoading] = useState(false);
  const [wavFromDate, setWavFromDate] = useState(
    formatDateStr(new Date(Date.now() - 7 * 86400000))
  );
  const [wavToDate, setWavToDate] = useState(formatDateStr(new Date()));
  const [wavCount, setWavCount] = useState<WavCountInfo | null>(null);
  const wsCleanupRef = useRef<(() => void) | null>(null);
  const [realtimeActive, setRealtimeActive] = useState(false);
  const [realtimeRemaining, setRealtimeRemaining] = useState(0);
  const realtimeTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const realtimeExpiryRef = useRef<number>(0);

  const REALTIME_DURATION_S = 300; // 5 minutes

  const sensorListRequest = useRef(0);
  const refreshSensors = useCallback((background = false) => {
    const request = ++sensorListRequest.current;
    if (!background) setLoading(true);
    setListError(false);
    Promise.all([apiListSensors(), apiListZones()])
      .then(([sensorRows, allowedZones]) => {
        if (request !== sensorListRequest.current) return;
        const allowed = new Set(allowedZones.map((zone) => zone.id));
        const data = sensorRows.filter((sensor) => sensor.zone_id && allowed.has(sensor.zone_id));
        setSelectedZone((current) => (current && !allowed.has(current) ? '' : current));
        setZones(allowedZones.sort((a, b) => a.name.localeCompare(b.name, 'de')));
        setSensors(data);
        setListError(false);
        if (typeof window !== 'undefined') {
          const params = new URLSearchParams(window.location.search);
          const zoneId = params.get('zone');
          if (zoneId && allowed.has(zoneId)) {
            setSelectedZone(zoneId);
            window.history.replaceState({}, '', window.location.pathname);
          }
          const sensorId = params.get('sensor');
          if (sensorId && data.some((s) => s.id === sensorId)) {
            setSelectedSensor(sensorId);
            // Clean up the URL
            const newUrl = window.location.pathname;
            window.history.replaceState({}, '', newUrl);
          }
        }
      })
      .catch((err) => {
        if (request !== sensorListRequest.current) return;
        console.error(err);
        setListError(true);
        setSensors([]);
        setZones([]);
        setSelectedSensor(null);
        setSensorData([]);
      })
      .finally(() => {
        if (request === sensorListRequest.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    refreshSensors();
    const timer = setInterval(() => refreshSensors(true), 30000);
    return () => clearInterval(timer);
  }, [refreshSensors]);

  useEffect(() => {
    if (selectedSensor && !sensors.some((sensor) => sensor.id === selectedSensor)) {
      setSelectedSensor(null);
      setSensorData([]);
    }
  }, [sensors, selectedSensor]);

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
      setDataError(false);
      hasInitialData.current = true;
    } catch (err) {
      console.error('Failed to load sensor data:', err);
      setDataError(true);
      if (!silent) setSensorData([]);
    } finally {
      if (!silent) setLoadingData(false);
    }
  }, []);

  // Background fetch to check electrode status for all sensors
  useEffect(() => {
    if (sensors.length === 0) return;

    let isMounted = true;
    const fetchStatuses = async () => {
      for (const s of sensors) {
        if (!isMounted) break;
        try {
          const data = await apiGetSensorDataAdvanced(s.id, { range: '5m', resolution: 'raw' });
          const bioSeries = data.find((d) => BIO_SIGNAL_KINDS.has(d.kind));
          if (isMounted) {
            if (bioSeries) {
              const status = detectElectrodeDisconnect(
                bioSeries.kind,
                bioSeries.unit,
                bioSeries.data
              );
              setElectrodeStatuses((prev) => ({ ...prev, [s.id]: status }));
            } else {
              setElectrodeStatuses((prev) => ({ ...prev, [s.id]: 'ok' }));
            }
          }
        } catch {
          console.error('Failed to fetch status for', s.id);
        }
        await new Promise((r) => setTimeout(r, 200)); // Rate limit
      }
    };
    fetchStatuses();
    return () => {
      isMounted = false;
    };
  }, [sensors]);

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

  const formatTimeWithSeconds = (t: string) => {
    const d = new Date(t);
    const h = d.getHours().toString().padStart(2, '0');
    const m = d.getMinutes().toString().padStart(2, '0');
    const s = d.getSeconds().toString().padStart(2, '0');
    return `${h}:${m}:${s}`;
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
    if (timeRange !== 'live') {
      setTimeRange('live');
      setBrushRanges({});
      if (selectedSensor) loadSensorData(selectedSensor, 'live');
    }
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
  }, [stopRealtime, timeRange, selectedSensor, loadSensorData]);

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
            const relevantReadings = msg.readings.filter((r) => r.kind === series.kind);
            if (relevantReadings.length === 0) return series;

            const newPoints = relevantReadings.map((r) => ({
              timestamp:
                typeof r.timestamp === 'string' ? r.timestamp : new Date(r.timestamp).toISOString(),
              value: r.value,
            }));

            const merged = [...series.data, ...newPoints].slice(-300);
            return { ...series, data: merged };
          });
        });
      },
      (connected) => setWsConnected(connected)
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

  // Load WAV count when a sensor is selected or date range changes
  const loadWavFiles = useCallback(
    async (sensorId: string) => {
      setWavLoading(true);
      const fromIso = new Date(wavFromDate + 'T00:00:00Z').toISOString();
      const toIso = new Date(wavToDate + 'T23:59:59Z').toISOString();
      try {
        const count = await apiCountWavFiles(sensorId, fromIso, toIso);
        setWavCount(count);
      } catch {
        setWavCount(null);
      } finally {
        setWavLoading(false);
      }
    },
    [wavFromDate, wavToDate]
  );

  useEffect(() => {
    if (!selectedSensor) {
      setWavCount(null);
      return;
    }
    loadWavFiles(selectedSensor);
  }, [selectedSensor, loadWavFiles]);

  const confirmDeleteSensor = async () => {
    if (!deletingSensorId) return;
    setIsDeleting(true);
    try {
      await apiDeleteSensor(deletingSensorId);
      setSensors((prev) => prev.filter((s) => s.id !== deletingSensorId));
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

  const toggleSmsAlerts = async () => {
    if (!selectedSensorInfo || isUpdatingSms) return;
    setIsUpdatingSms(true);
    const newValue = !selectedSensorInfo.sms_alerts_enabled;
    try {
      const updated = await apiUpdateSensor(selectedSensorInfo.id, {
        sms_alerts_enabled: newValue,
      });
      setSensors((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
    } catch (err) {
      console.error('Failed to update SMS alerts:', err);
      alert('Fehler beim Aktualisieren der SMS-Einstellungen.');
    } finally {
      setIsUpdatingSms(false);
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

  if (listError)
    return (
      <div className="glass-card p-8">
        <h1 className="text-2xl font-semibold text-gray-800">Messungen & Sensoren</h1>
        <p role="alert" className="mt-3 text-sm text-gray-600">
          Deine Sensoren konnten nicht geladen werden.
        </p>
        <button
          onClick={() => refreshSensors()}
          className="mt-4 px-5 py-3 rounded-xl bg-emerald-600 text-white text-sm"
        >
          Erneut laden
        </button>
      </div>
    );

  const selectedSensorInfo = sensors.find((s) => s.id === selectedSensor);

  const renderSensorDetailPanel = () => {
    if (!selectedSensor || !selectedSensorInfo) return null;
    return (
      <div className="w-full">
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

              <div className="flex flex-wrap items-center gap-2">
                {/* SMS Alerts Toggle */}
                <button
                  onClick={toggleSmsAlerts}
                  disabled={isUpdatingSms}
                  className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg transition-all border shrink-0 disabled:opacity-50 ${
                    selectedSensorInfo.sms_alerts_enabled
                      ? 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100'
                      : 'bg-gray-50 text-gray-500 border-gray-200 hover:bg-gray-100'
                  }`}
                  aria-pressed={selectedSensorInfo.sms_alerts_enabled}
                  title="SMS Warnungen bei Elektroden-Abfall"
                >
                  <span className="text-sm">📱</span>
                  {isUpdatingSms
                    ? 'Speichere...'
                    : selectedSensorInfo.sms_alerts_enabled
                      ? 'SMS aktiv'
                      : 'SMS stumm'}
                </button>

                {/* Time Range Segmented Control */}
                <div
                  role="group"
                  aria-label="Zeitraum"
                  className="flex flex-wrap bg-black/[0.03] rounded-xl p-0.5"
                >
                  {(['live', '1h', '24h', '7d', '30d'] as TimeRange[]).map((range) => (
                    <button
                      key={range}
                      onClick={() => handleRangeChange(range)}
                      aria-pressed={timeRange === range}
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
                          <span
                            className={`w-1.5 h-1.5 rounded-full ${timeRange === 'live' ? 'bg-white animate-pulse' : 'bg-emerald-500'}`}
                          />
                          Live
                        </span>
                      ) : (
                        { '1h': '1 Stunde', '24h': '24 Stunden', '7d': '7 Tage', '30d': '30 Tage' }[
                          range
                        ]
                      )}
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
                      <svg
                        width="14"
                        height="14"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                        <polyline points="7 10 12 15 17 10" />
                        <line x1="12" y1="15" x2="12" y2="3" />
                      </svg>
                      Daten herunterladen
                    </>
                  )}
                  {exportStatus === 'loading' && 'Lade…'}
                  {exportStatus === 'zipping' && 'Wird gepackt…'}
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

            {dataError && (
              <div
                role="alert"
                className="m-4 sm:m-6 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
              >
                Messdaten konnten nicht aktualisiert werden. Bereits sichtbare Werte können älter
                sein.
                <button
                  onClick={() => loadSensorData(selectedSensor, timeRange)}
                  className="block mt-2 underline underline-offset-4 font-medium"
                >
                  Erneut versuchen
                </button>
              </div>
            )}
            {/* Charts */}
            <div className="p-4 sm:p-6">
              {loadingData ? (
                <div className="grid grid-cols-1 gap-6">
                  {[1, 2, 3, 4].map((i) => (
                    <div key={i} className="animate-pulse h-56 bg-black/[0.03] rounded-2xl" />
                  ))}
                </div>
              ) : sensorData.length === 0 ? (
                <div className="py-16 text-center text-gray-400 text-sm">
                  {dataError
                    ? 'Messdaten derzeit nicht verfügbar'
                    : 'Keine Messdaten für diesen Zeitraum vorhanden'}
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-6">
                  {sensorData
                    .filter((s) => s.kind in KIND_CONFIG)
                    .map((series) => {
                      const config = KIND_CONFIG[series.kind];
                      const displayUnit = series.unit || config.unit;
                      const latestValue =
                        series.data.length > 0 ? series.data[series.data.length - 1].value : null;
                      const latestTimestamp = series.data.at(-1)?.timestamp;
                      const electrodeStatus = detectElectrodeDisconnect(
                        series.kind,
                        series.unit,
                        series.data
                      );

                      return (
                        <div
                          key={series.kind}
                          className={`bg-white rounded-xl p-4 sm:p-6 border ${
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
                                  Signal konstant bei{' '}
                                  {electrodeStatus === 'rail_high'
                                    ? '~3300 mV (Sättigung)'
                                    : '~0 mV (kein Kontakt)'}
                                  . Bitte Elektroden prüfen.
                                </p>
                              </div>
                            </div>
                          )}

                          {/* Chart Header */}
                          <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
                            <div className="flex items-center gap-2">
                              <span
                                className="w-2 h-2 rounded-full"
                                style={{ background: config.color }}
                                aria-hidden="true"
                              />
                              <span className="text-sm font-medium text-gray-700">
                                {config.label}
                              </span>
                            </div>
                            {latestValue !== null && (
                              <div className="text-right">
                                <span className="text-2xl font-medium tabular-nums text-gray-800">
                                  {latestValue}
                                </span>
                                <span className="text-xs text-gray-500 ml-1">{displayUnit}</span>
                                {latestTimestamp && (
                                  <p className="text-xs text-gray-500 mt-1">
                                    Letzter Messwert ·{' '}
                                    <time dateTime={latestTimestamp}>
                                      {new Date(latestTimestamp).toLocaleString(locale, {
                                        day: '2-digit',
                                        month: '2-digit',
                                        hour: '2-digit',
                                        minute: '2-digit',
                                        second: '2-digit',
                                      })}
                                    </time>
                                  </p>
                                )}
                              </div>
                            )}
                          </div>

                          {/* Chart */}
                          {series.data.length > 0 ? (
                            <SignalChart
                              key={series.sensor_id}
                              series={series}
                              color={config.color}
                              unit={displayUnit}
                              formatTick={(t) =>
                                timeRange === 'live'
                                  ? formatTimeWithSeconds(t)
                                  : timeRange === '1h' || timeRange === '24h'
                                    ? formatTime(t)
                                    : formatDate(t)
                              }
                            />
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
                <div className="mt-4 flex flex-wrap items-center justify-center gap-3 text-sm text-gray-500">
                  {realtimeActive ? (
                    <>
                      <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.4)]" />
                      <span className="text-emerald-600 font-medium">
                        Echtzeit {wsConnected ? 'verbunden' : 'verbindet…'}
                      </span>
                      <span className="font-mono text-emerald-500 bg-emerald-50 px-1.5 py-0.5 rounded">
                        {formatCountdown(realtimeRemaining)}
                      </span>
                      <button
                        onClick={stopRealtime}
                        className="px-2.5 py-1 bg-red-50 text-red-600 hover:bg-red-100 rounded-lg font-medium transition-colors border border-red-200/50"
                      >
                        Beenden
                      </button>
                    </>
                  ) : (
                    <>
                      <span className="w-2 h-2 rounded-full bg-gray-400 animate-pulse" />
                      Abfrage alle 5 Sekunden
                      <button
                        onClick={startRealtime}
                        className="ml-1 inline-flex items-center gap-1.5 px-3 py-1 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 rounded-lg font-medium transition-all duration-200 border border-emerald-200/50 hover:shadow-sm"
                      >
                        <svg
                          width="12"
                          height="12"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2.5"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                        </svg>
                        Echtzeit starten
                      </button>
                    </>
                  )}
                </div>
              )}

              {/* WAV Files Section */}
              <div className="mt-6 pt-6 border-t border-black/[0.04]">
                <div className="flex items-center gap-2 mb-3">
                  <span className="text-lg">🎵</span>
                  <span className="text-sm font-medium text-gray-700">
                    WAV-Dateien (380 Hz Rohdaten)
                  </span>
                </div>
                <p className="text-xs text-gray-400 mb-3">
                  Rohdaten 90 Tage · verifizierte Merkmale mindestens zwei Jahre
                </p>

                {/* Date Range + Download */}
                <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3 p-3 rounded-xl bg-black/[0.02] border border-black/[0.04]">
                  <div className="flex items-center gap-2 flex-wrap">
                    <label className="text-xs text-gray-500 font-medium">Von</label>
                    <input
                      type="date"
                      value={wavFromDate}
                      onChange={(e) => setWavFromDate(e.target.value)}
                      className="px-3 py-1.5 rounded-lg bg-white/80 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                    />
                    <label className="text-xs text-gray-500 font-medium">Bis</label>
                    <input
                      type="date"
                      value={wavToDate}
                      onChange={(e) => setWavToDate(e.target.value)}
                      max={formatDateStr(new Date())}
                      className="px-3 py-1.5 rounded-lg bg-white/80 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                    />
                  </div>

                  <div className="flex items-center gap-3 ml-auto">
                    {wavLoading ? (
                      <span className="text-xs text-gray-400 flex items-center gap-1.5">
                        <span className="w-3 h-3 border-2 border-gray-300 border-t-gray-500 rounded-full animate-spin" />
                        Lade…
                      </span>
                    ) : wavCount && wavCount.count > 0 ? (
                      <>
                        <span className="text-xs text-gray-400">
                          {wavCount.count.toLocaleString()} Datei{wavCount.count !== 1 ? 'en' : ''}
                          {wavCount.total_bytes > 0 && <> · {formatBytes(wavCount.total_bytes)}</>}
                        </span>
                        {selectedSensor && (
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
                                <svg
                                  width="12"
                                  height="12"
                                  viewBox="0 0 24 24"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="2"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                >
                                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                  <polyline points="7 10 12 15 17 10" />
                                  <line x1="12" y1="15" x2="12" y2="3" />
                                </svg>
                                Als ZIP herunterladen
                              </>
                            )}
                          </button>
                        )}
                      </>
                    ) : (
                      <span className="text-xs text-gray-400">
                        Keine Dateien im gewählten Zeitraum
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  };

  const renderSensorTable = (sensorList: SensorInfo[], isArchivedGroup = false) => (
    <div className="glass-table hidden xl:block">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-black/[0.04]">
            <th className="text-left px-5 py-3.5 text-[11px] font-medium text-gray-400 uppercase tracking-wider">
              Name
            </th>
            <th className="text-left px-5 py-3.5 text-[11px] font-medium text-gray-400 uppercase tracking-wider">
              MAC
            </th>
            <th className="text-left px-5 py-3.5 text-[11px] font-medium text-gray-400 uppercase tracking-wider">
              Gateway
            </th>
            <th className="text-left px-5 py-3.5 text-[11px] font-medium text-gray-400 uppercase tracking-wider">
              Status
            </th>
            <th className="text-left px-5 py-3.5 text-[11px] font-medium text-gray-400 uppercase tracking-wider">
              Zuletzt
            </th>
            <th className="text-left px-5 py-3.5 text-[11px] font-medium text-gray-400 uppercase tracking-wider w-12"></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-black/[0.03]">
          {sensorList.map((s) => (
            <Fragment key={s.id}>
              <tr
                onClick={() => handleSensorClick(s.id)}
                className={`cursor-pointer transition-all duration-200 ${
                  selectedSensor === s.id ? 'bg-emerald-50/60' : 'hover:bg-white/50'
                }`}
              >
                <td className="px-5 py-3.5 font-medium text-gray-800">
                  <div className="flex items-center gap-2">
                    <span
                      className={`transition-transform duration-200 text-xs text-gray-300 ${selectedSensor === s.id ? 'rotate-90' : ''}`}
                    >
                      ▶
                    </span>
                    <button
                      onClick={(event) => {
                        event.stopPropagation();
                        handleSensorClick(s.id);
                      }}
                      aria-expanded={selectedSensor === s.id}
                      className="text-left py-2"
                    >
                      {s.name || s.mac_address}
                    </button>
                    {isArchivedGroup && (
                      <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 text-[10px] font-medium border border-gray-200">
                        Archiviert
                      </span>
                    )}
                    {electrodeStatuses[s.id] && electrodeStatuses[s.id] !== 'ok' && (
                      <span
                        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-50 text-amber-600 text-[10px] font-bold border border-amber-200/50"
                        title={`Elektrode abgefallen (Signal ${electrodeStatuses[s.id] === 'rail_high' ? 'High' : 'Low'})`}
                      >
                        ⚠️ Warnung
                      </span>
                    )}
                  </div>
                </td>
                <td className="px-5 py-3.5 font-mono text-gray-400 text-xs">{s.mac_address}</td>
                <td className="px-5 py-3.5 text-gray-500">{s.gateway_name || '–'}</td>
                <td className="px-5 py-3.5">
                  <span
                    className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
                      s.status === 'online'
                        ? 'bg-emerald-50 text-emerald-600'
                        : 'bg-gray-100 text-gray-400'
                    }`}
                  >
                    <span
                      className={`w-1.5 h-1.5 rounded-full ${s.status === 'online' ? 'bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.4)]' : 'bg-gray-300'}`}
                    />
                    {isArchivedGroup ? 'inaktiv (>3d)' : s.status}
                  </span>
                </td>
                <td className="px-5 py-3.5 text-xs text-gray-400">
                  {s.last_seen ? new Date(s.last_seen).toLocaleString('de-CH') : '–'}
                </td>
                <td className="px-5 py-3.5">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setDeletingSensorId(s.id);
                    }}
                    className="p-1.5 text-gray-300 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all"
                    title="Sensor entfernen"
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                      />
                    </svg>
                  </button>
                </td>
              </tr>
              {selectedSensor === s.id && (
                <tr>
                  <td colSpan={6} className="p-0 border-b border-black/[0.04] bg-emerald-50/10">
                    <div className="p-0 sm:p-2 animate-in slide-in-from-top-2 duration-200">
                      {renderSensorDetailPanel()}
                    </div>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );

  const renderSensorMobileCards = (sensorList: SensorInfo[], isArchivedGroup = false) => (
    <div className="space-y-2 xl:hidden">
      {sensorList.map((s) => (
        <Fragment key={s.id}>
          <button
            type="button"
            aria-expanded={selectedSensor === s.id}
            onClick={() => handleSensorClick(s.id)}
            className={`glass-card w-full text-left p-4 cursor-pointer ${
              selectedSensor === s.id ? 'border-emerald-500/20' : ''
            } ${isArchivedGroup ? 'opacity-80 bg-gray-50/40' : ''}`}
          >
            <div className="flex items-center justify-between gap-2 mb-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-gray-800 break-all">
                  {s.name || s.mac_address}
                </span>
                <span
                  className={`w-2 h-2 rounded-full ${s.status === 'online' ? 'bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.4)]' : 'bg-gray-300'}`}
                />
              </div>
              {isArchivedGroup && (
                <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 text-[10px] font-medium border border-gray-200">
                  Archiviert
                </span>
              )}
            </div>
            <div className="text-xs text-gray-500">
              {s.status === 'online' ? 'Online' : 'Offline'} · {s.gateway_name || 'Kein Gateway'} ·{' '}
              {s.mac_address}
            </div>
            {electrodeStatuses[s.id] && electrodeStatuses[s.id] !== 'ok' && (
              <div className="mt-2 inline-flex items-center gap-1.5 px-2 py-1 rounded-lg bg-amber-50 text-amber-700 text-xs font-medium border border-amber-200/50">
                <span>⚠️</span>
                Elektrode abgefallen (Signal{' '}
                {electrodeStatuses[s.id] === 'rail_high' ? 'High' : 'Low'})
              </div>
            )}
          </button>
          {selectedSensor === s.id && (
            <div className="mt-1 mb-2 animate-in slide-in-from-top-2 duration-200">
              {renderSensorDetailPanel()}
            </div>
          )}
        </Fragment>
      ))}
    </div>
  );

  return (
    <div className="space-y-6 relative">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-800 tracking-tight">Messungen & Sensoren</h1>
          <p className="text-sm text-gray-400 mt-1">
            Deine freigegebenen Zonen mit Sensoren, Messwerten und Originalaufnahmen.
          </p>
        </div>
        <button
          onClick={() => setIsPairDialogOpen(true)}
          className="flex items-center justify-center gap-2 px-6 py-2.5 bg-emerald-600 text-white text-sm font-medium rounded-xl hover:bg-emerald-700 transition-colors text-center whitespace-nowrap"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          Sensor verbinden
        </button>
      </div>

      {zones.length > 1 && (
        <label className="block text-sm font-medium text-gray-700">
          Zone
          <select
            value={selectedZone}
            onChange={(event) => {
              setSelectedZone(event.target.value);
              setSelectedSensor(null);
              setSensorData([]);
            }}
            className="mt-2 block w-full max-w-sm rounded-xl border border-gray-200 bg-white px-3 py-2"
          >
            <option value="">Alle freigegebenen Zonen</option>
            {zones.map((zone) => (
              <option key={zone.id} value={zone.id}>
                {zone.name}
              </option>
            ))}
          </select>
        </label>
      )}
      {directFeed.error && (
        <p role="status" className="text-sm text-amber-800">
          Direkt verbundene Sensoren konnten nicht aktualisiert werden.
        </p>
      )}
      {zones.length === 0 ? (
        <div className="glass-card p-10 text-center">
          <h2 className="font-semibold text-gray-800">Keine Zonen freigegeben</h2>
          <p className="mt-2 text-sm text-gray-500">
            Sobald du Zugang zu einer Zone hast, erscheinen ihre Sensoren und Messungen hier.
          </p>
        </div>
      ) : (
        zones
          .filter((zone) => !selectedZone || zone.id === selectedZone)
          .map((zone) => {
            const active = activeSensors.filter((sensor) => sensor.zone_id === zone.id);
            const archived = archivedSensors.filter((sensor) => sensor.zone_id === zone.id);
            const devices = directFeed.devices.filter((device) => device.zone_id === zone.id);
            const count = active.length + archived.length + devices.length;
            return (
              <section
                key={zone.id}
                aria-labelledby={`zone-${zone.id}`}
                className="space-y-4 rounded-2xl border border-emerald-100 bg-white/60 p-4 sm:p-6"
              >
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-emerald-100 pb-4">
                  <div>
                    <h2 id={`zone-${zone.id}`} className="text-xl font-semibold text-gray-800">
                      {zone.name}
                    </h2>
                    {zone.location && <p className="mt-1 text-sm text-gray-500">{zone.location}</p>}
                  </div>
                  <span className="rounded-full bg-emerald-50 px-3 py-1 text-sm text-emerald-800">
                    {count} {count === 1 ? 'Sensor' : 'Sensoren'}
                  </span>
                </div>
                {devices.length > 0 && <DirectSensorsPanel feed={{ ...directFeed, devices }} />}
                {active.length > 0 && (
                  <div className="space-y-3">
                    <h3 className="text-sm font-medium text-gray-600">Über Gateway verbunden</h3>
                    {renderSensorTable(active)}
                    {renderSensorMobileCards(active)}
                  </div>
                )}
                {archived.length > 0 && (
                  <details className="rounded-xl border border-gray-200 bg-gray-50/60 p-4">
                    <summary className="cursor-pointer text-sm font-medium text-gray-600">
                      Inaktive Sensoren ({archived.length})
                    </summary>
                    <p className="my-3 text-xs text-gray-500">
                      Seit mehr als drei Tagen offline. Bisherige Messungen bleiben abrufbar.
                    </p>
                    {renderSensorTable(archived, true)}
                    {renderSensorMobileCards(archived, true)}
                  </details>
                )}
                {count === 0 && (
                  <p className="py-4 text-sm text-gray-500">
                    In dieser Zone sind noch keine Sensoren registriert.
                  </p>
                )}
              </section>
            );
          })
      )}

      {/* Delete Sensor Confirmation Modal */}
      {deletingSensorId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div
            className="absolute inset-0 bg-black/20 backdrop-blur-sm"
            onClick={() => setDeletingSensorId(null)}
          />
          <div className="relative bg-white/80 rounded-2xl border border-white/50 shadow-2xl backdrop-blur-xl p-6 max-w-sm w-full animate-in fade-in zoom-in duration-200">
            <div className="w-12 h-12 rounded-full bg-red-50 text-red-500 flex items-center justify-center mb-4 mx-auto">
              <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                />
              </svg>
            </div>
            <h3 className="text-lg font-semibold text-center text-gray-800 mb-2">
              Sensor entfernen?
            </h3>
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
