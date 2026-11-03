'use client';
import { readViewValue, replaceViewValues } from '@/lib/view-state';
import { initialMeasurementMode, finishMeasurementRestore } from '@/lib/measurement-view';
import ExportButton, { type ExportDevice } from './ExportButton';

import { useAppText } from '@/i18n/app-text';

import { useLocale } from 'next-intl';
import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  apiListSensors,
  apiListZones,
  Zone,
  apiGetSensorDataAdvanced,
  apiDeleteSensor,
  createSensorWebSocket,
  apiUpdateSensor,
  SensorInfo,
  SensorDataResponse,
} from '@/lib/api';
import SignalChart from './SignalChart';
import PairSensorDialog from './PairSensorDialog';
import DirectSensorsPanel, { useDirectDevices } from './DirectSensorsPanel';
import SensorCard from './SensorCard';
import ZoneSensorCard from './ZoneSensorCard';

type TimeRange = 'live' | '24h';

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

const THREE_DAYS_MS = 3 * 24 * 60 * 60 * 1000;

function isSensorArchived(s: SensorInfo): boolean {
  if (s.status === 'online' || s.status === 'disconnected') return false;

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

export default function SensorsPage() {
  const tx = useAppText();

  const locale = useLocale();
  const [listError, setListError] = useState(false);
  const [dataError, setDataError] = useState(false);
  const [sensors, setSensors] = useState<SensorInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedSensor, setSelectedSensor] = useState<string | null>(null);
  const [zones, setZones] = useState<Zone[]>([]);
  const [selectedZone, setSelectedZone] = useState('');
  const selectionRestored = useRef(false);
  const directFeed = useDirectDevices();

  useEffect(() => {
    if (!directFeed.available || directFeed.error || loading) return;
    const selected = readViewValue('direct');
    if (
      selected &&
      !directFeed.devices.some(
        (device) => device.id === selected && zones.some((zone) => zone.id === device.zone_id)
      )
    ) {
      replaceViewValues({ direct: null, directRange: null, directLiveWindow: null });
    }
  }, [directFeed.available, directFeed.error, directFeed.devices, loading, zones]);

  const activeSensors = useMemo(() => sensors.filter((s) => !isSensorArchived(s)), [sensors]);
  const archivedSensors = useMemo(() => sensors.filter((s) => isSensorArchived(s)), [sensors]);
  const [sensorData, setSensorData] = useState<SensorDataResponse[]>([]);
  const [loadingData, setLoadingData] = useState(false);
  const [timeRange, setTimeRange] = useState<TimeRange>(() => initialMeasurementMode('range'));
  const [liveSeconds, setLiveSeconds] = useState<30 | 60 | 180>(30);
  const [liveNow, setLiveNow] = useState(Date.now());
  const [pageVisible, setPageVisible] = useState(true);
  useEffect(() => {
    finishMeasurementRestore('range');
  }, []);
  useEffect(() => {
    const update = () => setPageVisible(document.visibilityState !== 'hidden');
    update();
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);
  useEffect(() => {
    if (timeRange !== 'live' || !pageVisible) return;
    const timer = setInterval(() => setLiveNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [timeRange, pageVisible]);
  const [exportWindow, setExportWindow] = useState<{
    sensorId: string;
    range: TimeRange;
    from_dt: string;
    to_dt: string;
  } | null>(null);
  const [electrodeStatuses, setElectrodeStatuses] = useState<Record<string, ElectrodeStatus>>({});
  const hasInitialData = useRef(false);
  const dataRequest = useRef(0);
  const [brushRanges, setBrushRanges] = useState<
    Record<string, { startIndex: number; endIndex: number }>
  >({});
  const [deletingSensorId, setDeletingSensorId] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isUpdatingSms, setIsUpdatingSms] = useState(false);
  const [isPairDialogOpen, setIsPairDialogOpen] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);

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
          }
          selectionRestored.current = true;
          const sensorId = params.get('sensor');
          if (sensorId && data.some((s) => s.id === sensorId)) {
            setSelectedSensor(sensorId);
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

  useEffect(() => {
    if (!selectionRestored.current) return;
    replaceViewValues({
      zone: selectedZone || null,
      sensor: selectedSensor,
      range: timeRange === '24h' ? null : timeRange,
    });
  }, [selectedZone, selectedSensor, timeRange, loading]);

  const loadSensorData = useCallback(async (sensorId: string, range: TimeRange, silent = false) => {
    const request = ++dataRequest.current;
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
        const to = new Date();
        const bounds = {
          from_dt: new Date(to.getTime() - 86400000).toISOString(),
          to_dt: to.toISOString(),
        };
        data = await apiGetSensorDataAdvanced(sensorId, { range: '24h', ...bounds });
        if (request === dataRequest.current) setExportWindow({ sensorId, range, ...bounds });
      }
      if (request !== dataRequest.current) return;
      setSensorData(data);
      setDataError(false);
      hasInitialData.current = true;
    } catch (err) {
      if (request !== dataRequest.current) return;
      console.error('Failed to load sensor data:', err);
      setDataError(true);
      if (!silent) setSensorData([]);
    } finally {
      if (!silent && request === dataRequest.current) setLoadingData(false);
    }
  }, []);

  useEffect(() => {
    if (selectedSensor) void loadSensorData(selectedSensor, timeRange);
    return () => {
      dataRequest.current += 1;
    };
  }, [selectedSensor, timeRange, loadSensorData]);

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
    setTimeRange('24h');
    setLiveSeconds(30);
    if (selectedSensor === sensorId) {
      setSelectedSensor(null);
      setSensorData([]);
    } else {
      setSelectedSensor(sensorId);
    }
    setBrushRanges({});
  };

  const handleRangeChange = (range: TimeRange) => {
    setTimeRange(range);
    setBrushRanges({});
  };

  const formatTime = (t: string) => {
    return new Intl.DateTimeFormat(tx.locale, { hour: '2-digit', minute: '2-digit' }).format(
      new Date(t)
    );
  };

  const formatTimeWithSeconds = (t: string) => {
    return new Intl.DateTimeFormat(tx.locale, {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).format(new Date(t));
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
    if (timeRange !== 'live' || !selectedSensor || !pageVisible) {
      stopRealtime();
    }
  }, [timeRange, selectedSensor, stopRealtime, pageVisible]);

  // WebSocket: only active during Realtime mode
  useEffect(() => {
    // Clean up any previous WebSocket
    if (wsCleanupRef.current) {
      wsCleanupRef.current();
      wsCleanupRef.current = null;
      setWsConnected(false);
    }

    if (!selectedSensor || timeRange !== 'live' || !realtimeActive || isZoomed || !pageVisible)
      return;

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

            const cutoff = Date.now() - 180000;
            const merged = [...series.data, ...newPoints]
              .filter((p) => Date.parse(p.timestamp) >= cutoff)
              .slice(-2000);
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
  }, [selectedSensor, timeRange, realtimeActive, isZoomed, pageVisible]);

  // Default live polling (5s) — always runs in live mode unless Realtime is active
  useEffect(() => {
    if (!selectedSensor || timeRange !== 'live' || isZoomed || realtimeActive || !pageVisible)
      return;
    const interval = setInterval(() => {
      loadSensorData(selectedSensor, 'live', true);
    }, 5000);
    return () => clearInterval(interval);
  }, [selectedSensor, timeRange, loadSensorData, isZoomed, realtimeActive, pageVisible]);

  const formatCountdown = (seconds: number): string => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
  };

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
      alert(tx('Fehler beim Aktualisieren der SMS-Einstellungen.'));
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
        <h1 className="text-2xl font-semibold text-gray-800">{tx('Messungen')}</h1>
        <p role="alert" className="mt-3 text-sm text-gray-600">
          {tx('Deine Sensoren konnten nicht geladen werden.')}
        </p>
        <button
          onClick={() => refreshSensors()}
          className="mt-4 px-5 py-3 rounded-xl bg-emerald-600 text-white text-sm"
        >
          {tx('Erneut laden')}
        </button>
      </div>
    );

  const selectedSensorInfo = sensors.find((s) => s.id === selectedSensor);
  const exportDevices: ExportDevice[] = [
    ...sensors.map((sensor) => ({
      id: sensor.id,
      kind: 'gateway' as const,
      name: sensor.name || sensor.mac_address,
    })),
    ...directFeed.devices
      .filter((device) => zones.some((zone) => zone.id === device.zone_id))
      .map((device) => ({
        id: device.id,
        kind: 'direct' as const,
        name: `Biolingo ${device.hardware_id?.slice(-5).replace(':', '') || device.id.slice(0, 8)}`,
      })),
  ];

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
              </div>

              <div className="flex flex-wrap items-center gap-2">
                {/* Time Range Segmented Control */}
                <div
                  role="group"
                  aria-label={tx('Zeitraum')}
                  className="flex flex-wrap bg-black/[0.03] rounded-xl p-0.5"
                >
                  {(['24h', 'live'] as TimeRange[]).map((range) => (
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
                          {tx('Live')}
                        </span>
                      ) : (
                        tx(
                          {
                            '24h': '24 Stunden',
                          }[range]
                        )
                      )}
                    </button>
                  ))}
                </div>

                <ExportButton
                  devices={exportDevices}
                  initialKey={`gateway:${selectedSensor}`}
                  initialBounds={
                    exportWindow?.sensorId === selectedSensor ? exportWindow : undefined
                  }
                />
              </div>
            </div>

            {dataError && (
              <div
                role="alert"
                className="m-4 sm:m-6 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
              >
                {tx(
                  'Messdaten konnten nicht aktualisiert werden. Bereits sichtbare Werte können älter sein.'
                )}
                <button
                  onClick={() => loadSensorData(selectedSensor, timeRange)}
                  className="block mt-2 underline underline-offset-4 font-medium"
                >
                  {tx('Erneut versuchen')}
                </button>
              </div>
            )}
            <details className="mx-4 mt-3 text-xs text-gray-500 sm:mx-6">
              <summary className="cursor-pointer">{tx('Geräteeinstellungen')}</summary>
              <p className="my-2">
                {selectedSensorInfo.mac_address} · {selectedSensorInfo.gateway_name}
              </p>
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
                title={tx('SMS Warnungen bei Elektroden-Abfall')}
              >
                <span className="text-sm">📱</span>
                {isUpdatingSms
                  ? tx('Speichere...')
                  : selectedSensorInfo.sms_alerts_enabled
                    ? tx('SMS aktiv')
                    : tx('SMS stumm')}
              </button>
            </details>
            {timeRange === 'live' && (
              <div className="mx-4 mt-4 space-y-2 sm:mx-6">
                <div role="group" aria-label={tx('Livefenster')} className="flex flex-wrap gap-2">
                  {([30, 60, 180] as const).map((value) => (
                    <button
                      type="button"
                      key={value}
                      aria-pressed={liveSeconds === value}
                      onClick={() => setLiveSeconds(value)}
                      className={`rounded-lg border px-3 py-2 text-sm ${liveSeconds === value ? 'bg-emerald-700 text-white' : 'bg-white text-gray-600'}`}
                    >
                      {tx(value === 30 ? '30 Sekunden' : value === 60 ? '1 Minute' : '3 Minuten')}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-gray-500">
                  {tx(
                    'Dieses Gateway liefert keine Rail-Prozentwerte oder Live-Markierungen. Die schnelle Vorschau ist bei geeigneten Direct-Sensoren verfügbar.'
                  )}
                </p>
                {!pageVisible && (
                  <p role="status">{tx('Live pausiert, solange dieser Tab ausgeblendet ist.')}</p>
                )}
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
                    ? tx('Messdaten derzeit nicht verfügbar')
                    : tx('Keine Messdaten für diesen Zeitraum vorhanden')}
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-6">
                  {sensorData
                    .filter((s) => s.kind in KIND_CONFIG)
                    .map((storedSeries) => {
                      const series =
                        timeRange === 'live'
                          ? {
                              ...storedSeries,
                              time_info: undefined,
                              data: storedSeries.data.filter(
                                (p) => Date.parse(p.timestamp) >= liveNow - liveSeconds * 1000
                              ),
                            }
                          : storedSeries;
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
                                  {tx('Elektrode abgefallen?')}
                                </p>
                                <p className="text-xs text-amber-600 mt-0.5">
                                  {tx('Signal konstant bei')}{' '}
                                  {electrodeStatus === 'rail_high'
                                    ? tx('~3300 mV (Sättigung)')
                                    : tx('~0 mV (kein Kontakt)')}
                                  {tx('. Bitte Elektroden prüfen.')}
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
                                {tx(config.label)}
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
                                    {timeRange === 'live' && (
                                      <span className="block">
                                        {tx('Signalalter: {value0} ms', {
                                          value0: Math.max(
                                            0,
                                            liveNow - Date.parse(latestTimestamp)
                                          ),
                                        })}
                                      </span>
                                    )}
                                    {tx('Letzter Messwert ·')}{' '}
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
                                timeRange === 'live' ? formatTimeWithSeconds(t) : formatTime(t)
                              }
                            />
                          ) : (
                            <div className="h-40 flex items-center justify-center text-gray-300 text-xs">
                              {tx('Keine Daten')}
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
                        {tx('Echtzeit')}
                        {wsConnected ? tx('verbunden') : tx('verbindet…')}
                      </span>
                      <span className="font-mono text-emerald-500 bg-emerald-50 px-1.5 py-0.5 rounded">
                        {formatCountdown(realtimeRemaining)}
                      </span>
                      <button
                        onClick={stopRealtime}
                        className="px-2.5 py-1 bg-red-50 text-red-600 hover:bg-red-100 rounded-lg font-medium transition-colors border border-red-200/50"
                      >
                        {tx('Beenden')}
                      </button>
                    </>
                  ) : (
                    <>
                      <span className="w-2 h-2 rounded-full bg-gray-400 animate-pulse" />
                      {tx('Abfrage alle 5 Sekunden')}
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
                        {tx('Echtzeit starten')}
                      </button>
                    </>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    );
  };

  const renderSensorCards = (sensorList: SensorInfo[], archived = false) => (
    <div className="space-y-3">
      {sensorList.map((sensor) => (
        <SensorCard
          key={sensor.id}
          name={sensor.name || sensor.mac_address}
          connection={`${tx('Gateway')}${sensor.gateway_name ? ` · ${sensor.gateway_name}` : ''} · ${sensor.mac_address}`}
          status={
            sensor.status === 'disconnected'
              ? tx('Getrennt')
              : archived
                ? 'Inaktiv (>3 Tage)'
                : sensor.status === 'online'
                  ? tx('Empfängt Daten')
                  : tx('Offline')
          }
          online={sensor.status === 'online'}
          lastSeen={sensor.last_seen}
          expanded={selectedSensor === sensor.id}
          onToggle={() => handleSensorClick(sensor.id)}
          notice={
            electrodeStatuses[sensor.id] && electrodeStatuses[sensor.id] !== 'ok'
              ? 'Elektrodenkontakt prüfen'
              : undefined
          }
        >
          {renderSensorDetailPanel()}
          <details className="mt-3 text-xs text-gray-500">
            <summary className="cursor-pointer">{tx('Geräteeinstellungen')}</summary>
            <button
              type="button"
              onClick={() => setDeletingSensorId(sensor.id)}
              className="mt-3 rounded-lg px-3 py-2 text-xs text-gray-500 hover:bg-red-50 hover:text-red-600"
            >
              {tx('Sensor entfernen')}
            </button>
          </details>
        </SensorCard>
      ))}
    </div>
  );

  return (
    <div className="space-y-6 relative">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-800 tracking-tight">{tx('Messungen')}</h1>
          <p className="text-sm text-gray-400 mt-1">
            {tx('Gerät auswählen und Messungen ansehen.')}
          </p>
        </div>
        <ExportButton
          devices={exportDevices}
          initialKey={selectedSensor ? `gateway:${selectedSensor}` : undefined}
        />
      </div>

      <details className="text-sm text-gray-600">
        <summary className="cursor-pointer">{tx('Geräte verwalten')}</summary>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <a className="text-emerald-800 underline" href={`/${locale}/app/manage`}>
            {tx('Verwaltung öffnen')}
          </a>
          <button
            onClick={() => setIsPairDialogOpen(true)}
            className="flex items-center justify-center gap-2 px-6 py-2.5 bg-emerald-600 text-white text-sm font-medium rounded-xl hover:bg-emerald-700 transition-colors text-center whitespace-nowrap"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 4v16m8-8H4"
              />
            </svg>
            {tx('Sensor verbinden')}
          </button>{' '}
        </div>
      </details>
      {zones.length > 1 && (
        <label className="block text-sm font-medium text-gray-700">
          {tx('Zone')}
          <select
            value={selectedZone}
            onChange={(event) => {
              setSelectedZone(event.target.value);
              setSelectedSensor(null);
              setSensorData([]);
            }}
            className="mt-2 block w-full max-w-sm rounded-xl border border-gray-200 bg-white px-3 py-2"
          >
            <option value="">{tx('Alle freigegebenen Zonen')}</option>
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
          {tx('Direkt verbundene Sensoren konnten nicht aktualisiert werden.')}
        </p>
      )}
      {zones.length === 0 ? (
        <div className="glass-card p-10 text-center">
          <h2 className="font-semibold text-gray-800">{tx('Keine Zonen freigegeben')}</h2>
          <p className="mt-2 text-sm text-gray-500">
            {tx(
              'Sobald du Zugang zu einer Zone hast, erscheinen ihre Sensoren und Messungen hier.'
            )}
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
              <ZoneSensorCard
                key={zone.id}
                name={zone.name}
                location={zone.location}
                count={count}
                connections={[
                  ...Array.from(
                    new Set(
                      [...active, ...archived].map((sensor) => sensor.gateway_name || tx('Gateway'))
                    )
                  ).map((name) => (name === tx('Gateway') ? name : `${tx('Gateway')} · ${name}`)),
                  ...(devices.length ? ['Direct-to-Cloud'] : []),
                ].join(' · ')}
                reveal={
                  selectedZone === zone.id ||
                  [...active, ...archived].some((sensor) => sensor.id === selectedSensor) ||
                  devices.some((device) => device.id === readViewValue('direct'))
                }
                onCollapse={() => {
                  if ([...active, ...archived].some((sensor) => sensor.id === selectedSensor)) {
                    setSelectedSensor(null);
                    setSensorData([]);
                  }
                }}
              >
                {active.length > 0 && renderSensorCards(active)}
                {devices.length > 0 && <DirectSensorsPanel feed={{ ...directFeed, devices }} />}
                {archived.length > 0 && (
                  <details className="rounded-xl border border-gray-200 bg-gray-50/60 p-4">
                    <summary className="cursor-pointer text-sm font-medium text-gray-600">
                      {tx('Inaktive Sensoren (')}
                      {archived.length})
                    </summary>
                    <p className="my-3 text-xs text-gray-500">
                      {tx(
                        'Seit mehr als drei Tagen offline. Bisherige Messungen bleiben abrufbar.'
                      )}
                    </p>
                    {renderSensorCards(archived, true)}
                  </details>
                )}
                {count === 0 && (
                  <p className="py-4 text-sm text-gray-500">
                    {tx('In dieser Zone sind noch keine Sensoren registriert.')}
                  </p>
                )}
              </ZoneSensorCard>
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
              {tx('Sensor entfernen?')}
            </h3>
            <p className="text-sm text-center text-gray-500 mb-6">
              {tx('Alle Messdaten dieses Sensors werden unwiderruflich gelöscht.')}
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setDeletingSensorId(null)}
                className="flex-1 px-4 py-2.5 bg-white border border-gray-200 text-gray-700 rounded-xl text-sm font-medium hover:bg-gray-50 transition-colors"
              >
                {tx('Abbrechen')}
              </button>
              <button
                onClick={confirmDeleteSensor}
                disabled={isDeleting}
                className="flex-1 px-4 py-2.5 bg-red-500 text-white rounded-xl text-sm font-medium hover:bg-red-600 transition-colors disabled:opacity-50"
              >
                {isDeleting ? tx('Lösche…') : tx('Löschen')}
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
