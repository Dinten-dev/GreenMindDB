'use client';
import { readViewValue, replaceViewValues } from '@/lib/view-state';

import { useAppText } from '@/i18n/app-text';
import { useEffect, useState } from 'react';
import type { SensorDataResponse } from '@/lib/api';
import { initialMeasurementMode, finishMeasurementRestore } from '@/lib/measurement-view';
import SignalChart from './SignalChart';
import DirectLiveView from './DirectLiveView';
import SensorCard from './SensorCard';
import ExportButton from './ExportButton';

export type DirectDevice = {
  id: string;
  hardware_id: string | null;
  zone_id: string;
  status: 'online' | 'offline' | 'disabled';
  last_seen: string | null;
  spool_bytes: number;
};
class ReadError extends Error {
  constructor(readonly status: number) {
    super('Messdaten konnten nicht geladen werden. Bitte erneut versuchen.');
  }
}
function shouldClearCachedData(error: unknown) {
  return (
    error instanceof ReadError &&
    error.status >= 400 &&
    error.status < 500 &&
    error.status !== 408 &&
    error.status !== 429
  );
}

async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, { cache: 'no-store', signal });
  if (!response.ok) throw new ReadError(response.status);
  return response.json();
}

function DirectMeasurements({
  device,
  devices,
}: {
  device: DirectDevice;
  devices: DirectDevice[];
}) {
  const tx = useAppText();
  const [isLive, setIsLive] = useState(() => initialMeasurementMode('directRange') === 'live');
  const [series, setSeries] = useState<SensorDataResponse[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [viewBounds, setViewBounds] = useState<{ from_dt: string; to_dt: string } | null>(null);
  const base = `/api/v1/visualization/direct/${device.id}`;
  useEffect(() => {
    finishMeasurementRestore('directRange');
  }, []);
  useEffect(() => {
    replaceViewValues({
      directRange: isLive ? 'live' : null,
      ...(isLive ? {} : { directLiveWindow: null }),
    });
  }, [isLive]);
  useEffect(() => {
    if (isLive) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setLoading(true);
    setSeries([]);
    setViewBounds(null);
    setError('');
    const refresh = async () => {
      try {
        const end = new Date();
        const bounds = {
          from_dt: new Date(end.getTime() - 86400000).toISOString(),
          to_dt: end.toISOString(),
        };
        const data = await read<SensorDataResponse[]>(
          `${base}/data?${new URLSearchParams({ range: '24h', ...bounds })}`,
          controller.signal
        );
        if (controller.signal.aborted) return;
        setSeries(data);
        setViewBounds(bounds);
        setError('');
      } catch (failure) {
        if (!controller.signal.aborted) {
          if (shouldClearCachedData(failure)) {
            setSeries([]);
            setViewBounds(null);
          }
          setError(
            tx(
              'Messdaten konnten nicht aktualisiert werden. Angezeigte Werte können veraltet sein.'
            )
          );
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          timer = setTimeout(refresh, 10000);
        }
      }
    };
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [tx, base, retry, isLive]);
  return (
    <div
      className="space-y-4 rounded-xl bg-[#fcfdfb] p-1 sm:p-2"
      aria-label={tx('Direct-Messdaten')}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="group" aria-label={tx('Zeitraum')} className="flex gap-2">
          {(['24h', 'live'] as const).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={isLive === (value === 'live')}
              onClick={() => setIsLive(value === 'live')}
              className={`rounded-lg border px-4 py-2 text-sm ${isLive === (value === 'live') ? 'border-emerald-700 bg-emerald-700 text-white' : 'border-gray-200 bg-white text-gray-600'}`}
            >
              {tx(value === '24h' ? '24 Stunden' : 'Live')}
            </button>
          ))}
        </div>
        <ExportButton
          devices={devices.map((d) => ({
            id: d.id,
            kind: 'direct',
            name: `Biolingo ${d.hardware_id?.slice(-5).replace(':', '') || d.id.slice(0, 8)}`,
          }))}
          initialKey={`direct:${device.id}`}
          initialBounds={viewBounds ?? undefined}
        />
      </div>
      {isLive ? (
        <DirectLiveView
          deviceId={device.id}
          onHistory={() => setIsLive(false)}
          showHistoryButton={false}
        />
      ) : (
        <>
          {error && (
            <div role="alert" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
              {error}{' '}
              <button type="button" onClick={() => setRetry((n) => n + 1)} className="underline">
                {tx('Erneut laden')}
              </button>
            </div>
          )}
          {loading ? (
            <p role="status" className="py-8 text-sm text-gray-500">
              {tx('Messwerte werden geladen …')}
            </p>
          ) : !series.some((item) => item.data.length) ? (
            error ? null : (
              <p role="status" className="py-8 text-sm text-gray-500">
                {tx('Keine Messdaten für diesen Zeitraum vorhanden')}
              </p>
            )
          ) : (
            series.map((item) => (
              <section key={item.kind} aria-label={tx('Signal {value0}', { value0: item.kind })}>
                {series.length > 1 && (
                  <h3 className="mb-2 text-sm font-medium">
                    {tx('Kanal')} {item.kind.split('ch')[1]}
                  </h3>
                )}
                {item.analytics_scope === 'comparison' && (
                  <p className="text-xs text-amber-800">
                    {tx('DUAL-Vergleichsmessung: separat von Gateway-Werten dargestellt.')}
                  </p>
                )}
                <SignalChart
                  series={item}
                  color="#10b981"
                  unit={item.unit}
                  formatTick={(stamp) =>
                    new Date(stamp).toLocaleTimeString(tx.locale, {
                      hour: '2-digit',
                      minute: '2-digit',
                    })
                  }
                />
              </section>
            ))
          )}
          <details className="text-xs text-gray-500">
            <summary className="cursor-pointer">{tx('Technische Details')}</summary>
            <p className="mt-2">{tx('Aktualisiert sich alle zehn Sekunden.')}</p>
            <p>
              {tx(
                'Die angegebene Auflösung gilt für die Übersicht. Mittelwert, Ausschläge, RMS und Datenabdeckung bleiben bei der Verdichtung erhalten. Die Originalaufnahmen behalten sämtliche empfangenen Messwerte.'
              )}
            </p>
            <p>{tx('Ältere Messungen und Originalaufnahmen findest du unter Exportieren.')}</p>
          </details>
        </>
      )}
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
      } catch (failure) {
        if (active) {
          if (shouldClearCachedData(failure)) setDevices([]);
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
    if (!feed || valid || devices.some((device) => device.id === readViewValue('direct'))) {
      replaceViewValues({ direct: valid });
    }
  }, [available, error, devices, selected, feed]);
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
          <DirectMeasurements device={device} devices={devices} />
        </SensorCard>
      ))}
    </div>
  );
}
