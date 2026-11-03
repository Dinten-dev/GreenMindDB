'use client';

import { useAppText } from '@/i18n/app-text';
import { useEffect, useState } from 'react';
import { adminStorage, type StorageStatus } from '@/lib/administration-api';
const bytes = (value: number, locale: string) =>
  (value / 1024 ** 3).toLocaleString(locale, { maximumFractionDigits: 1 }) + ' GB';
export default function StorageMonitor() {
  const tx = useAppText();

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
        {tx('Serverspeicher')}
      </h2>
      {error ? (
        <p role="status" className="text-sm text-gray-600">
          {tx(
            'Speicherstatus momentan nicht verfügbar. Die Anzeige wird automatisch erneut geladen.'
          )}
        </p>
      ) : !data ? (
        <p role="status" className="text-sm text-gray-500">
          {tx('Speicherstatus wird geladen …')}
        </p>
      ) : (
        <>
          {data.free_percent < 5 && data.warning && (
            <div
              role="alert"
              className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
            >
              <strong>{tx('Speicher wird knapp:')}</strong> {tx('Weniger als')}{' '}
              {data.warning_threshold_percent} {tx('% frei. Bitte Speicherbelegung prüfen.')}
            </div>
          )}
          <div className="flex flex-wrap justify-between gap-3">
            <p className="text-2xl font-semibold text-gray-800">
              {data.used_percent.toLocaleString(tx.locale, { maximumFractionDigits: 1 })}{' '}
              {tx('% belegt')}
            </p>
            <p className="text-sm text-gray-600">
              {bytes(data.available_bytes, tx.locale)} {tx('von')}{' '}
              {bytes(data.total_bytes, tx.locale)} {tx('frei')}
            </p>
          </div>
          <div
            role="meter"
            aria-label={tx('Belegter Serverspeicher')}
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
            {tx('Für Dienste verfügbar:')}{' '}
            {data.free_percent.toLocaleString(tx.locale, { maximumFractionDigits: 2 })}{' '}
            {tx('%. Stand:')} {new Date(data.checked_at).toLocaleString(tx.locale)}
            {tx('. Aktualisierung jede Minute.')}
          </p>
        </>
      )}
    </section>
  );
}
