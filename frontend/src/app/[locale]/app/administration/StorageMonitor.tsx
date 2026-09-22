'use client';
import { useEffect, useState } from 'react';
import { adminStorage, type StorageStatus } from '@/lib/administration-api';
const bytes = (value: number) =>
  (value / 1024 ** 3).toLocaleString('de-CH', { maximumFractionDigits: 1 }) + ' GB';
export default function StorageMonitor() {
  const [data, setData] = useState<StorageStatus | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const next = await adminStorage();
        if (active) {
          setData(next);
          setError(false);
        }
      } catch {
        if (active) {
          setData(null);
          setError(true);
        }
      } finally {
        if (active) timer = setTimeout(refresh, 60000);
      }
    };
    void refresh();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, []);
  return (
    <section className="glass-card p-6 space-y-4" aria-labelledby="storage-title">
      <h2 id="storage-title" className="text-lg font-semibold text-gray-800">
        Serverspeicher
      </h2>
      {error ? (
        <p role="status" className="text-sm text-gray-600">
          Speicherstatus momentan nicht verfügbar. Die Anzeige wird automatisch erneut geladen.
        </p>
      ) : !data ? (
        <p role="status" className="text-sm text-gray-500">
          Speicherstatus wird geladen …
        </p>
      ) : (
        <>
          {data.free_percent < 5 && data.warning && (
            <div
              role="alert"
              className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
            >
              <strong>Speicher wird knapp:</strong> Weniger als {data.warning_threshold_percent} %
              frei. Bitte Speicherbelegung prüfen.
            </div>
          )}
          <div className="flex flex-wrap justify-between gap-3">
            <p className="text-2xl font-semibold text-gray-800">
              {data.used_percent.toLocaleString('de-CH', { maximumFractionDigits: 1 })} % belegt
            </p>
            <p className="text-sm text-gray-600">
              {bytes(data.available_bytes)} von {bytes(data.total_bytes)} frei
            </p>
          </div>
          <div
            role="meter"
            aria-label="Belegter Serverspeicher"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={data.used_percent}
            className="h-3 overflow-hidden rounded-full bg-gray-100"
          >
            <div
              className={`h-full rounded-full ${data.warning ? 'bg-amber-500' : 'bg-emerald-600'}`}
              style={{ width: `${data.used_percent}%` }}
            />
          </div>
          <p className="text-xs text-gray-500">
            Für Dienste verfügbar:{' '}
            {data.free_percent.toLocaleString('de-CH', { maximumFractionDigits: 2 })} %. Stand:{' '}
            {new Date(data.checked_at).toLocaleString('de-CH')}. Aktualisierung jede Minute.
          </p>
        </>
      )}
    </section>
  );
}
