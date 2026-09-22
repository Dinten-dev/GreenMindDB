'use client';

import { useEffect, useState } from 'react';
import type { SensorDataResponse } from '@/lib/api';
import SignalChart from './SignalChart';

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
  duration_seconds: number;
  sample_rate: number;
};
const ranges = [
  ['5m', 'Live · 5 Min.'],
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
  const [range, setRange] = useState('24h');
  const [series, setSeries] = useState<SensorDataResponse[]>([]);
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const base = `/api/v1/visualization/direct/${device.id}`;
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let first = true;
    setLoading(true);
    setSeries([]);
    setRecordings([]);
    setError('');
    const refresh = async () => {
      try {
        const [data, files] = await Promise.all([
          read<SensorDataResponse[]>(`${base}/data?range=${range}`, controller.signal),
          read<Recording[]>(`${base}/recordings?range=${range}`, controller.signal),
        ]);
        if (controller.signal.aborted) return;
        setSeries(data);
        setRecordings(files);
        setError('');
      } catch {
        if (!controller.signal.aborted)
          setError(
            'Messdaten konnten nicht aktualisiert werden. Angezeigte Werte können veraltet sein.'
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
  }, [base, range, retry]);
  const points = series.reduce((sum, item) => sum + item.data.length, 0);
  const latest = series
    .flatMap((item) => item.data)
    .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
    .at(-1);
  return (
    <div
      className="mt-4 rounded-2xl border border-emerald-100 bg-[#fcfdfb] p-4 sm:p-6"
      aria-label="Direct-Messdaten"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-semibold text-gray-900">
            Pflanzensignal · Biolingo {device.hardware_id?.slice(-5).replace(':', '')}
          </h3>
          <p className="mt-1 text-xs text-gray-500">Aktualisiert sich alle zehn Sekunden.</p>
        </div>
        <a
          href={`${base}/export?range=${range}`}
          className="rounded-lg border border-emerald-200 px-3 py-2 text-sm font-medium text-emerald-800"
        >
          CSV herunterladen
        </a>
      </div>
      <div className="my-4 flex flex-wrap gap-2" aria-label="Zeitraum">
        {ranges.map(([value, label]) => (
          <button
            key={value}
            type="button"
            aria-pressed={range === value}
            onClick={() => setRange(value)}
            className={`rounded-lg px-3 py-2 text-sm ${range === value ? 'bg-emerald-700 text-white' : 'bg-white text-gray-600 border border-gray-200'}`}
          >
            {label}
          </button>
        ))}
      </div>
      {error && (
        <div role="alert" className="mb-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
          {error}
          <button type="button" onClick={() => setRetry((n) => n + 1)} className="ml-2 underline">
            Erneut laden
          </button>
        </div>
      )}
      {loading ? (
        <p role="status" className="py-8 text-sm text-gray-500">
          Messwerte werden geladen …
        </p>
      ) : points === 0 ? (
        <p role="status" className="py-8 text-sm text-gray-500">
          {device.last_seen
            ? 'In diesem Zeitraum sind noch keine aufbereiteten Messwerte vorhanden. Neue Daten erscheinen nach wenigen Sekunden; ältere Messungen findest du über einen längeren Zeitraum.'
            : 'Der Sensor ist verbunden. Sobald erste Messwerte eintreffen, erscheint hier der Verlauf.'}
        </p>
      ) : (
        <>
          {latest && (
            <p className="mb-3 text-xs text-gray-500">
              Letzter Messpunkt: {new Date(latest.timestamp).toLocaleString('de-CH')}
            </p>
          )}
          {series.map((item) => (
            <section key={item.kind} className="mb-4" aria-label={`Signal ${item.kind}`}>
              {series.length > 1 && (
                <h4 className="mb-2 text-sm font-medium">Kanal {item.kind.split('ch')[1]}</h4>
              )}
              {item.analytics_scope === 'comparison' && (
                <p className="mb-2 text-xs text-amber-800">
                  DUAL-Vergleichsmessung: separat von Gateway-Werten dargestellt.
                </p>
              )}
              <SignalChart
                series={item}
                color="#10b981"
                unit={item.unit}
                formatTick={(stamp) =>
                  new Date(stamp).toLocaleString(
                    'de-CH',
                    range === '5m' || range === '1h'
                      ? { hour: '2-digit', minute: '2-digit' }
                      : { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }
                  )
                }
              />
            </section>
          ))}
          <p className="text-xs leading-relaxed text-gray-500">
            Die angegebene Auflösung gilt für die Übersicht. Mittelwert, Ausschläge, RMS und
            Datenabdeckung bleiben bei der Verdichtung erhalten. Die Originalaufnahmen behalten
            sämtliche empfangenen Messwerte.
          </p>
        </>
      )}
      <details className="mt-5 border-t border-emerald-100 pt-4">
        <summary className="cursor-pointer text-sm font-medium text-emerald-800">
          Originalaufnahmen · WAV ({recordings.length}
          {recordings.length === 100 ? '+' : ''})
        </summary>
        <p className="my-3 text-xs text-gray-500">
          Aufnahmen entstehen automatisch nach Abschluss eines Zeitfensters. Bei laufender
          Übertragung dauert dies bis zu zehn Minuten. Angezeigt werden die neuesten 100 Dateien im
          gewählten Zeitraum.
        </p>
        {recordings.length === 0 ? (
          <p className="text-sm text-gray-500">
            Noch keine abgeschlossene Aufnahme in diesem Zeitraum.
          </p>
        ) : (
          <ul className="max-h-64 divide-y divide-gray-100 overflow-auto">
            {recordings.map((file) => (
              <li
                key={`${file.segment_id}-${file.revision}-${file.run}`}
                className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm"
              >
                <span>
                  {new Date(file.started_at).toLocaleString('de-CH')}{' '}
                  <span className="text-gray-500">
                    · {Math.round(file.duration_seconds)} s · {file.sample_rate} Hz
                  </span>
                </span>
                <a
                  className="font-medium text-emerald-800 underline"
                  href={`${base}/wav/${file.segment_id}/${file.revision}/${file.run}`}
                >
                  WAV herunterladen
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
  const ownFeed = useDirectDevices(!feed);
  const { devices, available, error } = feed ?? ownFeed;
  const [selected, setSelected] = useState<string | null>(null);
  if (!available)
    return error ? (
      <p role="status" className="text-sm text-amber-800">
        Direkt verbundene Sensoren konnten nicht geladen werden.
      </p>
    ) : null;
  const chosen =
    devices.find((device) => device.id === selected) ??
    (selected === null ? devices[0] : undefined);
  return (
    <section
      className="rounded-2xl border border-emerald-100 bg-white p-6"
      aria-label="Direkt verbundene Sensoren"
    >
      <h2 className="text-lg font-semibold text-gray-900">Direkt über WLAN</h2>
      <p className="mt-1 text-sm text-gray-500">
        Wähle einen Sensor, um sein Signal, die Datenabdeckung und Originalaufnahmen anzusehen.
      </p>
      {error && (
        <p role="alert" className="mt-3 text-sm text-amber-800">
          Empfangsstatus konnte nicht aktualisiert werden. Die Anzeige kann veraltet sein.
        </p>
      )}
      {devices.length === 0 ? (
        <p className="mt-5 text-gray-600">
          Noch kein Sensor verbunden. Wähle beim Hinzufügen „Direkt über WLAN“.
        </p>
      ) : (
        <>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {devices.map((device) => (
              <button
                type="button"
                key={device.id}
                aria-pressed={chosen?.id === device.id}
                onClick={() => setSelected(device.id)}
                className={`rounded-xl border p-4 text-left transition-colors ${chosen?.id === device.id ? 'border-emerald-500 bg-emerald-50/60' : 'border-gray-200 hover:bg-gray-50'}`}
              >
                <span className="block font-medium text-gray-900">
                  Biolingo {device.hardware_id?.slice(-5).replace(':', '') || device.id.slice(0, 8)}
                </span>
                <span
                  className={`mt-1 block text-sm ${device.status === 'online' && !error ? 'text-emerald-800' : 'text-gray-500'}`}
                >
                  {error
                    ? 'Status unklar'
                    : device.status === 'online'
                      ? 'Empfängt Daten'
                      : device.status === 'disabled'
                        ? 'Deaktiviert'
                        : 'Keine aktuellen Daten'}
                </span>
                <span className="mt-2 block text-xs text-gray-500">
                  {device.last_seen
                    ? `Letzter Empfang: ${new Date(device.last_seen).toLocaleString('de-CH')}`
                    : 'Wartet auf erste Messdaten'}
                </span>
              </button>
            ))}
          </div>
          {chosen && <DirectMeasurements key={chosen.id} device={chosen} />}
        </>
      )}
    </section>
  );
}
