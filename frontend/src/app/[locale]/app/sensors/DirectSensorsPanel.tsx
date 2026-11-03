'use client';
import { readViewValue, replaceViewValues } from '@/lib/view-state';

import { RecordingTimeDetails } from './CaptureTimeSummary';
import { captureTimeZone, formatCaptureTime } from '@/lib/capture-time';
import type { CaptureTimeInfo } from '@/lib/api';

import { useAppText } from '@/i18n/app-text';

import RecordingCompletenessBadge from './RecordingCompletenessBadge';
import { nominalCompleteness } from '@/lib/recordingCompleteness';
import type { RecordingCompleteness } from '@/lib/api';

import { useEffect, useState } from 'react';
import type { SensorDataResponse } from '@/lib/api';
import SignalChart from './SignalChart';
import DirectLiveView from './DirectLiveView';
import SensorCard from './SensorCard';

export type DirectDevice = {
  id: string;
  hardware_id: string | null;
  zone_id: string;
  status: 'online' | 'offline' | 'disabled';
  last_seen: string | null;
  spool_bytes: number;
};
type Recording = {
  segment_id: string;
  revision: number;
  run: number;
  started_at: string;
  time_info?: CaptureTimeInfo;
  duration_seconds: number;
  completeness?: RecordingCompleteness;
  sample_rate: number;
};
const ranges = [
  ['5m', '5 Minuten'],
  ['1h', '1 Stunde'],
  ['24h', '24 Stunden'],
  ['7d', '7 Tage'],
  ['30d', '30 Tage'],
] as const;

async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, { cache: 'no-store', signal });
  if (!response.ok)
    throw new Error('Messdaten konnten nicht geladen werden. Bitte erneut versuchen.');
  return response.json();
}

function DirectMeasurements({ device }: { device: DirectDevice }) {
  const [isLive, setIsLive] = useState(() => readViewValue('directRange') === 'live');
  const tx = useAppText();

  const [range, setRange] = useState(() => {
    const value = readViewValue('directRange');
    return value && ranges.some(([key]) => key === value) ? value : '24h';
  });
  useEffect(() => {
    replaceViewValues({ directRange: isLive ? 'live' : range === '24h' ? null : range });
  }, [range, isLive]);
  const [series, setSeries] = useState<SensorDataResponse[]>([]);
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [viewBounds, setViewBounds] = useState<{ from_dt: string; to_dt: string } | null>(null);
  const base = `/api/v1/visualization/direct/${device.id}`;
  useEffect(() => {
    if (isLive) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let first = true;
    setLoading(true);
    setSeries([]);
    setRecordings([]);
    setViewBounds(null);
    setError('');
    const refresh = async () => {
      try {
        const end = new Date();
        const duration =
          { '5m': 300, '1h': 3600, '24h': 86400, '7d': 604800, '30d': 2592000 }[range] ?? 86400;
        const bounds = {
          from_dt: new Date(end.getTime() - duration * 1000).toISOString(),
          to_dt: end.toISOString(),
        };
        const [data, files] = await Promise.all([
          read<SensorDataResponse[]>(
            `${base}/data?${new URLSearchParams({ range, ...bounds })}`,
            controller.signal
          ),
          read<Recording[]>(`${base}/recordings?range=${range}`, controller.signal),
        ]);
        if (controller.signal.aborted) return;
        setSeries(data);
        setViewBounds(bounds);
        setRecordings(files);
        setError('');
      } catch {
        if (!controller.signal.aborted)
          setError(
            tx(
              'Messdaten konnten nicht aktualisiert werden. Angezeigte Werte können veraltet sein.'
            )
          );
      } finally {
        if (!controller.signal.aborted) {
          if (first) setLoading(false);
          first = false;
          timer = setTimeout(refresh, 10000);
        }
      }
    };
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [tx, base, range, retry, isLive]);
  if (isLive)
    return (
      <DirectLiveView
        deviceId={device.id}
        onHistory={() => {
          replaceViewValues({ directLiveWindow: null });
          setIsLive(false);
          setRange('24h');
        }}
      />
    );
  const points = series.reduce((sum, item) => sum + item.data.length, 0);
  const latest = series
    .flatMap((item) => item.data)
    .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
    .at(-1);
  return (
    <div className="rounded-xl bg-[#fcfdfb] p-1 sm:p-2" aria-label={tx('Direct-Messdaten')}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-semibold text-gray-900">
            {tx('Pflanzensignal · Biolingo')}
            {device.hardware_id?.slice(-5).replace(':', '')}
          </h3>
          <p className="mt-1 text-xs text-gray-500">
            {tx('Aktualisiert sich alle zehn Sekunden.')}
          </p>
        </div>
        <a
          href={`${base}/export?${new URLSearchParams({ range, ...(viewBounds ?? {}) })}`}
          className="rounded-lg border border-emerald-200 px-3 py-2 text-sm font-medium text-emerald-800"
        >
          {tx('CSV herunterladen')}
        </a>
      </div>
      <div className="my-4 flex flex-wrap gap-2" aria-label={tx('Zeitraum')}>
        <button
          type="button"
          aria-pressed={false}
          onClick={() => setIsLive(true)}
          className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-600"
        >
          {tx('Live')}
        </button>
        {ranges.map(([value, label]) => (
          <button
            key={value}
            type="button"
            aria-pressed={range === value}
            onClick={() => setRange(value)}
            className={`rounded-lg px-3 py-2 text-sm ${range === value ? 'bg-emerald-700 text-white' : 'bg-white text-gray-600 border border-gray-200'}`}
          >
            {tx(label)}
          </button>
        ))}
      </div>
      {error && (
        <div role="alert" className="mb-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
          {error}
          <button type="button" onClick={() => setRetry((n) => n + 1)} className="ml-2 underline">
            {tx('Erneut laden')}
          </button>
        </div>
      )}
      {loading ? (
        <p role="status" className="py-8 text-sm text-gray-500">
          {tx('Messwerte werden geladen …')}
        </p>
      ) : points === 0 ? (
        <p role="status" className="py-8 text-sm text-gray-500">
          {device.last_seen
            ? tx(
                'In diesem Zeitraum sind noch keine aufbereiteten Messwerte vorhanden. Neue Daten erscheinen nach wenigen Sekunden; ältere Messungen findest du über einen längeren Zeitraum.'
              )
            : tx(
                'Der Sensor ist verbunden. Sobald erste Messwerte eintreffen, erscheint hier der Verlauf.'
              )}
        </p>
      ) : (
        <>
          {latest && (
            <p className="mb-3 text-xs text-gray-500">
              {tx('Letzter Messpunkt:')}
              {formatCaptureTime(latest.timestamp, tx.locale)}
            </p>
          )}
          {series.map((item) => (
            <section
              key={item.kind}
              className="mb-4"
              aria-label={tx('Signal {value0}', { value0: item.kind })}
            >
              {series.length > 1 && (
                <h4 className="mb-2 text-sm font-medium">
                  {tx('Kanal')} {item.kind.split('ch')[1]}
                </h4>
              )}
              {item.analytics_scope === 'comparison' && (
                <p className="mb-2 text-xs text-amber-800">
                  {tx('DUAL-Vergleichsmessung: separat von Gateway-Werten dargestellt.')}
                </p>
              )}
              <SignalChart
                series={item}
                color="#10b981"
                unit={item.unit}
                formatTick={(stamp) =>
                  new Date(stamp).toLocaleString(
                    tx.locale,
                    range === '5m' || range === '1h'
                      ? { hour: '2-digit', minute: '2-digit' }
                      : { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }
                  )
                }
              />
            </section>
          ))}
          <p className="text-xs leading-relaxed text-gray-500">
            {tx(
              'Die angegebene Auflösung gilt für die Übersicht. Mittelwert, Ausschläge, RMS und Datenabdeckung bleiben bei der Verdichtung erhalten. Die Originalaufnahmen behalten sämtliche empfangenen Messwerte.'
            )}
          </p>
        </>
      )}
      <details className="mt-5 border-t border-emerald-100 pt-4">
        <summary className="cursor-pointer text-sm font-medium text-emerald-800">
          {tx('Originalaufnahmen · WAV (')}
          {recordings.length}
          {recordings.length === 100 ? '+' : ''})
        </summary>
        <p className="my-3 text-xs text-gray-500">
          {tx(
            'Aufnahmen entstehen automatisch nach Abschluss eines Zeitfensters. Bei laufender Übertragung dauert dies bis zu zehn Minuten. Angezeigt werden die neuesten 100 Dateien im gewählten Zeitraum.'
          )}
        </p>
        {recordings.length === 0 ? (
          <p className="text-sm text-gray-500">
            {tx('Noch keine abgeschlossene Aufnahme in diesem Zeitraum.')}
          </p>
        ) : (
          <ul className="max-h-64 divide-y divide-gray-100 overflow-auto">
            {recordings.map((file) => (
              <li
                key={`${file.segment_id}-${file.revision}-${file.run}`}
                className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm"
              >
                <div>
                  {formatCaptureTime(file.started_at, tx.locale)}{' '}
                  <span className="text-gray-500">
                    · {captureTimeZone()} · {Math.round(file.duration_seconds)} {tx('s ·')}{' '}
                    {file.sample_rate} {tx('Hz')}
                    <RecordingCompletenessBadge completeness={nominalCompleteness(file, true)} />
                  </span>
                  <RecordingTimeDetails time={file.time_info} />
                </div>
                <a
                  className="font-medium text-emerald-800 underline"
                  href={`${base}/wav/${file.segment_id}/${file.revision}/${file.run}`}
                >
                  {tx('WAV herunterladen')}
                </a>
              </li>
            ))}
          </ul>
        )}
      </details>
    </div>
  );
}

export function useDirectDevices(enabled = true) {
  const [devices, setDevices] = useState<DirectDevice[]>([]);
  const [available, setAvailable] = useState(false);
  const [error, setError] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let refreshing = false;
    const controller = new AbortController();
    const refresh = async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        const data = await read<DirectDevice[]>('/api/v1/direct-ingest/devices', controller.signal);
        if (active) {
          setDevices(data);
          setAvailable(true);
          setError(false);
        }
      } catch {
        if (active) {
          setDevices([]);
          setError(true);
        }
      } finally {
        refreshing = false;
      }
    };
    void refresh();
    const timer = setInterval(refresh, 10000);
    return () => {
      active = false;
      controller.abort();
      clearInterval(timer);
    };
  }, [enabled]);
  return { devices, available, error };
}

export default function DirectSensorsPanel({
  feed,
}: { feed?: ReturnType<typeof useDirectDevices> } = {}) {
  const tx = useAppText();

  const ownFeed = useDirectDevices(!feed);
  const { devices, available, error } = feed ?? ownFeed;
  const [selected, setSelected] = useState<string | null>(() => readViewValue('direct'));
  useEffect(() => {
    if (!available || error) return;
    const valid = selected && devices.some((device) => device.id === selected) ? selected : null;
    if (selected !== valid) setSelected(valid);
    replaceViewValues({ direct: valid });
  }, [available, error, devices, selected]);
  if (!available)
    return error ? (
      <p role="status" className="text-sm text-amber-800">
        {tx('Direkt verbundene Sensoren konnten nicht geladen werden.')}
      </p>
    ) : null;
  return (
    <div className="space-y-3">
      {error && (
        <p role="alert" className="text-sm text-amber-800">
          {tx('Empfangsstatus konnte nicht aktualisiert werden.')}
        </p>
      )}
      {devices.map((device) => (
        <SensorCard
          key={device.id}
          name={`Biolingo ${device.hardware_id?.slice(-5).replace(':', '') || device.id.slice(0, 8)}`}
          connection={tx('Direct-to-Cloud · Direkt über WLAN')}
          status={
            error
              ? tx('Status unklar')
              : device.status === 'online'
                ? tx('Empfängt Daten')
                : device.status === 'disabled'
                  ? tx('Deaktiviert')
                  : tx('Offline')
          }
          online={device.status === 'online' && !error}
          lastSeen={device.last_seen}
          expanded={selected === device.id}
          onToggle={() => {
            replaceViewValues({ directRange: null, directLiveWindow: null });
            setSelected(selected === device.id ? null : device.id);
          }}
        >
          <DirectMeasurements device={device} />
        </SensorCard>
      ))}
    </div>
  );
}
